"""Phase 7 of PATIENT_PORTAL_DESIGN.md — the tests that prove the design.

These are not "does the endpoint return 200" tests. Each one asserts a specific
security property that the portal would be worthless without:

  * IDOR      — patient A cannot read patient B's case, by any route
  * RLS       — the database itself refuses, even for a raw SELECT
  * Leak      — internal fields never reach a patient response
  * Audit     — every disclosure and every denial is recorded
  * Fail-safe — an unset RLS context returns nothing, rather than everything

Requires `python dbmigration/migrate.py` to have been run.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from carebridge.models import (
    CaseStatus,
    DataSource,
    DischargeDisposition,
    TransitionCase,
)
from carebridge.persistence import Database

STAFF_TOKEN = "test-staff-token"


def _make_case(patient_id: str, case_id: str) -> TransitionCase:
    return TransitionCase(
        case_id=case_id,
        patient_id=patient_id,
        admitting_facility="Test General",
        discharge_date=date(2026, 7, 10),
        discharge_disposition=DischargeDisposition.HOME,
        primary_diagnosis="Pneumonia, resolved",
        has_pcp_on_file=True,
        payer="SecretPayerCo",  # must never appear in a portal response
        referral_specialty="pulmonology",
        risk_flags=[],
        status=CaseStatus.NEEDS_REVIEW,
        source=DataSource.SYNTHETIC,
        source_message_id="test-msg-001",
        received_at=datetime.now(timezone.utc),
    )


@pytest.fixture(scope="module")
def env():
    """Portal keys + staff token. Set before the app or crypto module is used."""
    os.environ.setdefault("PORTAL_ENC_KEY", "HezZownmhO7UZrcFIT2Zr_gIKPRGUCznb0R3GdiJ0Dw=")
    os.environ.setdefault("PORTAL_HMAC_KEY", "ZSefGxbI4V2lGt4K-tSVZM3IxYOKINYqdhUm2nNZi7w=")
    os.environ["STAFF_API_TOKEN"] = STAFF_TOKEN
    os.environ["PORTAL_COOKIE_SECURE"] = "false"
    # This suite asserts the *secure* path. PORTAL_DEV_MODE bypasses staff auth
    # and lets a patient sign in with a bare username, so pin it off here rather
    # than inherit whatever .env happens to say.
    os.environ["PORTAL_DEV_MODE"] = "false"
    yield


@pytest.fixture(scope="module")
def db(env):
    database = Database()
    try:
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM portal.portal_case_view LIMIT 1"))
    except (OperationalError, ProgrammingError):
        pytest.skip("portal schema absent — run `python dbmigration/migrate.py`")
    return database


@pytest.fixture()
def two_patients(db):
    """Alice and Bob, each with one case. The whole suite is about keeping them
    apart."""
    suffix = uuid.uuid4().hex[:8]
    alice_pid, bob_pid = f"pt-alice-{suffix}", f"pt-bob-{suffix}"
    alice_cid, bob_cid = f"case-alice-{suffix}", f"case-bob-{suffix}"

    db.upsert_case(_make_case(alice_pid, alice_cid))
    db.upsert_case(_make_case(bob_pid, bob_cid))

    yield {
        "alice_pid": alice_pid, "alice_cid": alice_cid,
        "bob_pid": bob_pid, "bob_cid": bob_cid,
    }

    with db.engine.begin() as conn:
        for cid in (alice_cid, bob_cid):
            conn.execute(text("DELETE FROM events WHERE case_id = :c"), {"c": cid})
            conn.execute(text("DELETE FROM agent_decisions WHERE case_id = :c"), {"c": cid})
            conn.execute(text("DELETE FROM case_workflow WHERE case_id = :c"), {"c": cid})
            conn.execute(text("DELETE FROM cases WHERE case_id = :c"), {"c": cid})
        for pid in (alice_pid, bob_pid):
            conn.execute(text("DELETE FROM portal.portal_session WHERE patient_id = :p"), {"p": pid})
            conn.execute(text("DELETE FROM portal.login_token WHERE patient_id = :p"), {"p": pid})
            conn.execute(text("DELETE FROM portal.enrollment_token WHERE patient_id = :p"), {"p": pid})
            conn.execute(text("DELETE FROM portal.portal_user WHERE patient_id = :p"), {"p": pid})


# ---------------------------------------------------------------------------
# Layer 3 — the database refuses on its own
# ---------------------------------------------------------------------------


def test_portal_role_cannot_touch_cases_table_at_all(two_patients):
    """The portal's DB role has no privilege on `cases`. Not row-limited —
    absent. A SQL injection in a portal query still cannot read the table."""
    from carebridge.portal.repository import portal_engine

    with pytest.raises(ProgrammingError, match="permission denied"):
        with portal_engine().connect() as conn:
            conn.execute(text("SELECT * FROM public.cases"))


def test_rls_confines_a_raw_select_to_one_patient(two_patients):
    """Bypass the repository entirely: a raw SELECT over the whole view, as the
    portal role, still returns only the patient named by app.patient_id."""
    from carebridge.portal.repository import portal_engine

    with portal_engine().begin() as conn:
        conn.execute(
            text("SELECT set_config('app.patient_id', :p, true)"),
            {"p": two_patients["alice_pid"]},
        )
        rows = conn.execute(text("SELECT patient_id FROM portal.portal_case_view")).all()

    assert rows, "Alice should see her own case"
    assert {r.patient_id for r in rows} == {two_patients["alice_pid"]}


def test_unset_rls_context_returns_nothing_not_everything(two_patients):
    """Fail closed. Forgetting set_config must yield zero rows, because
    `patient_id = NULL` matches nothing — never an unfiltered table scan."""
    from carebridge.portal.repository import portal_engine

    with portal_engine().begin() as conn:
        rows = conn.execute(text("SELECT patient_id FROM portal.portal_case_view")).all()
    assert rows == []


def test_phi_access_log_is_append_only(db, two_patients):
    """UPDATE and DELETE are revoked from the runtime roles: an attacker holding
    the app's credentials still cannot rewrite the record of what they read."""
    from carebridge.portal.repository import portal_engine

    with pytest.raises(ProgrammingError, match="permission denied"):
        with portal_engine().begin() as conn:
            conn.execute(text("DELETE FROM portal.phi_access_log"))


# ---------------------------------------------------------------------------
# Layer 2 — the repository chokepoint
# ---------------------------------------------------------------------------


def test_repository_will_not_return_another_patients_case(two_patients):
    """The IDOR attempt, at the layer beneath HTTP: ask for Bob's case_id while
    scoped to Alice."""
    from carebridge.portal.repository import fetch_my_case, fetch_my_cases

    assert fetch_my_case(two_patients["alice_pid"], two_patients["alice_cid"]) is not None
    assert fetch_my_case(two_patients["alice_pid"], two_patients["bob_cid"]) is None

    alice_case_ids = {c.case_id for c in fetch_my_cases(two_patients["alice_pid"])}
    assert two_patients["alice_cid"] in alice_case_ids
    assert two_patients["bob_cid"] not in alice_case_ids


# ---------------------------------------------------------------------------
# Layer 1 — over HTTP, end to end
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(env, db):
    """LLM_AVAILABLE=false keeps lifespan from constructing LLM agents, so these
    tests need no API key and no Ollama."""
    os.environ["LLM_AVAILABLE"] = "false"
    from fastapi.testclient import TestClient

    from carebridge.api.main import app

    with TestClient(app) as c:
        yield c


def _sign_in(client, db, patient_id: str, email: str) -> None:
    """Walk the real flow: staff issues an enrollment token out of band, the
    patient redeems it, then signs in with a magic link. Sets the session cookie
    on `client`."""
    from carebridge.portal import auth

    r = client.post(
        "/api/portal/admin/enrollment-tokens",
        json={"patient_id": patient_id},
        headers={"Authorization": f"Bearer {STAFF_TOKEN}"},
    )
    assert r.status_code == 201, r.text
    enrollment_token = r.json()["enrollment_token"]

    r = client.post("/api/portal/auth/enroll", json={"token": enrollment_token, "email": email})
    assert r.status_code == 201, r.text

    # The login link would be emailed; grab it directly rather than parse a log.
    login_token = auth.request_login_token(db.engine, email=email)
    assert login_token
    r = client.post("/api/portal/auth/session", json={"token": login_token})
    assert r.status_code == 200, r.text


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/api/portal/me/cases").status_code == 401


def test_idor_over_http_returns_404_not_403(client, db, two_patients):
    """The headline test. Alice signs in and asks for Bob's case by id.

    404, not 403: a 403 would confirm the case exists, turning the endpoint into
    an enumeration oracle for other patients' case ids.
    """
    _sign_in(client, db, two_patients["alice_pid"], f"alice-{two_patients['alice_pid']}@example.com")

    own = client.get(f"/api/portal/me/cases/{two_patients['alice_cid']}")
    assert own.status_code == 200

    theirs = client.get(f"/api/portal/me/cases/{two_patients['bob_cid']}")
    assert theirs.status_code == 404
    assert "not found" in theirs.json()["detail"].lower()


def test_case_list_contains_only_own_cases(client, db, two_patients):
    _sign_in(client, db, two_patients["alice_pid"], f"alice2-{two_patients['alice_pid']}@example.com")
    ids = {c["case_id"] for c in client.get("/api/portal/me/cases").json()}
    assert ids == {two_patients["alice_cid"]}


FORBIDDEN_FIELDS = (
    "confidence", "rationale", "payer", "source_message_id", "admitting_facility",
    "risk_flags", "snapshot", "internal_status", "agent_decisions", "audit",
)


def test_portal_response_leaks_no_internal_fields(client, db, two_patients):
    """Guards §7 of the design against a careless edit to PortalCaseOut. Checks
    the raw response text, so a nested or renamed leak is still caught."""
    _sign_in(client, db, two_patients["alice_pid"], f"alice3-{two_patients['alice_pid']}@example.com")

    for path in ("/api/portal/me/cases", f"/api/portal/me/cases/{two_patients['alice_cid']}"):
        body = client.get(path).text.lower()
        for field in FORBIDDEN_FIELDS:
            assert field not in body, f"{path} leaked {field!r}"
        # The payer value itself, not just the key name.
        assert "secretpayerco" not in body
        # Internal status strings must be mapped, never passed through.
        assert "needs_review" not in body


def test_internal_status_is_mapped_to_patient_language(client, db, two_patients):
    _sign_in(client, db, two_patients["alice_pid"], f"alice4-{two_patients['alice_pid']}@example.com")
    case = client.get(f"/api/portal/me/cases/{two_patients['alice_cid']}").json()
    assert case["status"] == "in_review"  # from internal "needs_review"
    assert "care coordinator" in case["status_message"].lower()


def test_status_is_never_ready_until_a_clinician_approves(client, db, two_patients):
    """The agent pipeline marks a case `completed` as soon as the agents agree,
    long before anybody signs it. If that drove the patient's label, they would
    read "Your care plan is ready" above an empty summary panel."""
    with db.engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status = 'completed' WHERE case_id = :c"),
            {"c": two_patients["alice_cid"]},
        )

    _sign_in(client, db, two_patients["alice_pid"], f"alice7-{two_patients['alice_pid']}@example.com")
    case = client.get(f"/api/portal/me/cases/{two_patients['alice_cid']}").json()

    assert case["summary"] is None, "no summary without approval"
    assert case["status"] != "ready", "label must not contradict the empty summary"
    assert case["status"] == "in_review"

    # Now approve it, and only now may the label say ready.
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO case_workflow (case_id, uploaded_by, summary_text, "
                "approved_by, approved_at, created_at, updated_at) "
                "VALUES (:c, 'dr.test', 'Signed summary.', 'admin.test', now(), now(), now()) "
                "ON CONFLICT (case_id) DO UPDATE SET summary_text = 'Signed summary.', "
                "approved_by = 'admin.test', approved_at = now()"
            ),
            {"c": two_patients["alice_cid"]},
        )

    case = client.get(f"/api/portal/me/cases/{two_patients['alice_cid']}").json()
    assert case["status"] == "ready"
    assert case["summary"] == "Signed summary."


def test_every_read_and_every_denial_is_audited(client, db, two_patients):
    """HIPAA §164.312(b). The denial row is the one that matters: it is how you
    detect somebody walking case ids."""
    _sign_in(client, db, two_patients["alice_pid"], f"alice5-{two_patients['alice_pid']}@example.com")

    with db.engine.begin() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM portal.phi_access_log WHERE actor_id = :a"),
            {"a": two_patients["alice_pid"]},
        ).scalar_one()

    client.get(f"/api/portal/me/cases/{two_patients['alice_cid']}")  # allow
    client.get(f"/api/portal/me/cases/{two_patients['bob_cid']}")    # deny

    with db.engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT action, outcome, case_id FROM portal.phi_access_log "
                "WHERE actor_id = :a ORDER BY id DESC LIMIT 2"
            ),
            {"a": two_patients["alice_pid"]},
        ).all()
        after = conn.execute(
            text("SELECT count(*) FROM portal.phi_access_log WHERE actor_id = :a"),
            {"a": two_patients["alice_pid"]},
        ).scalar_one()

    assert after == before + 2
    outcomes = {(r.action, r.outcome, r.case_id) for r in rows}
    assert ("read_case", "allow", two_patients["alice_cid"]) in outcomes
    assert ("read_case", "deny", two_patients["bob_cid"]) in outcomes


def test_staff_routes_reject_missing_and_wrong_tokens(client, two_patients):
    body = {"patient_id": two_patients["alice_pid"]}
    assert client.post("/api/portal/admin/enrollment-tokens", json=body).status_code == 401
    assert client.post(
        "/api/portal/admin/enrollment-tokens", json=body,
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401


def test_log_endpoints_are_no_longer_public(client):
    """Finding #2: these streamed the internal event log to anyone."""
    assert client.get("/api/logs/tail").status_code == 401
    assert client.get(
        "/api/logs/tail", headers={"Authorization": f"Bearer {STAFF_TOKEN}"}
    ).status_code == 200


def test_enrollment_token_is_single_use(client, db, two_patients):
    r = client.post(
        "/api/portal/admin/enrollment-tokens",
        json={"patient_id": two_patients["bob_pid"]},
        headers={"Authorization": f"Bearer {STAFF_TOKEN}"},
    )
    token = r.json()["enrollment_token"]
    email = f"bob-{two_patients['bob_pid']}@example.com"

    assert client.post("/api/portal/auth/enroll", json={"token": token, "email": email}).status_code == 201
    # Replay must fail, with a message that does not reveal the token was real.
    replay = client.post("/api/portal/auth/enroll", json={"token": token, "email": email})
    assert replay.status_code == 400


def test_login_does_not_reveal_whether_an_account_exists(client):
    """Account existence is itself PHI: it says this person was discharged here."""
    known = client.post("/api/portal/auth/login", json={"email": "nobody@example.com"})
    assert known.status_code == 200
    assert "if that email is registered" in known.json()["status"].lower()


def test_logout_revokes_the_session(client, db, two_patients):
    _sign_in(client, db, two_patients["alice_pid"], f"alice6-{two_patients['alice_pid']}@example.com")
    assert client.get("/api/portal/me/cases").status_code == 200
    assert client.post("/api/portal/auth/logout").status_code == 200
    assert client.get("/api/portal/me/cases").status_code == 401


# ---------------------------------------------------------------------------
# Dev mode — the "just username + role" shortcut
# ---------------------------------------------------------------------------


def test_dev_session_route_does_not_exist_unless_dev_mode(client, two_patients):
    """With PORTAL_DEV_MODE off (as pinned by the env fixture) the shortcut is a
    404 — not a 403, so it does not even advertise that it exists."""
    r = client.post("/api/portal/auth/dev-session", json={"username": two_patients["alice_pid"]})
    assert r.status_code == 404


def test_dev_session_signs_in_with_a_bare_username(env, db, two_patients, monkeypatch):
    """With dev mode on, a username alone mints a session — and the row-level
    security boundary still holds for what that session can then read."""
    monkeypatch.setenv("PORTAL_DEV_MODE", "true")
    from fastapi.testclient import TestClient

    from carebridge.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/portal/auth/dev-session", json={"username": two_patients["alice_pid"]})
        assert r.status_code == 200, r.text
        assert r.json()["patient_id"] == two_patients["alice_pid"]

        # Alice sees her own case...
        ids = {x["case_id"] for x in c.get("/api/portal/me/cases").json()}
        assert ids == {two_patients["alice_cid"]}

        # ...and still cannot reach Bob's, shortcut sign-in or not.
        assert c.get(f"/api/portal/me/cases/{two_patients['bob_cid']}").status_code == 404
