from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from carebridge.api.main import app
from carebridge.llm import OllamaClient
from carebridge.persistence import Database


@pytest.fixture(scope="module")
def client():
    try:
        Database().init_schema()
    except OperationalError:
        pytest.skip("Postgres not reachable — run `docker compose up -d` in backend/")
    if not OllamaClient().is_reachable():
        pytest.skip("Ollama not reachable — run `ollama serve`")

    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_fixtures(client):
    response = client.get("/api/fixtures")
    assert response.status_code == 200
    keys = {f["key"] for f in response.json()}
    assert keys == {"clean", "payer_delay", "high_risk"}


def test_create_clean_case_auto_completes(client):
    response = client.post("/api/cases", json={"template": "clean"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "auto_completed"

    detail = client.get(f"/api/cases/{body['case_id']}").json()
    agent_names = {d["agent_name"] for d in detail["agent_decisions"]}
    assert agent_names == {
        "referral_routing",
        "followup_scheduling",
        "medication_instruction",
        "patient_outreach",
        "discharge_readiness",
        "risk_escalation",
    }
    assert detail["pending_review"] is None


def test_bulk_ingest_mixed_batch(client):
    import time
    from datetime import date, timedelta

    suffix = uuid4().hex[:6]
    base = {
        "discharge_date": date.today().isoformat(),
        "discharge_disposition": "home",
        "primary_diagnosis": "Pneumonia, resolved",
        "has_pcp_on_file": True,
        "payer": "Aetna",
        "referral_specialty": "pulmonology",
    }
    good_a = {**base, "case_id": f"bulk-a-{suffix}"}
    good_b = {**base, "case_id": f"bulk-b-{suffix}"}
    bad_date = {**base, "discharge_date": (date.today() + timedelta(days=90)).isoformat()}
    dupe_in_batch = {**base, "case_id": f"bulk-a-{suffix}"}

    response = client.post("/api/cases/ingest", json=[good_a, good_b, bad_date, dupe_in_batch])
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["accepted"], body["rejected"]) == (4, 2, 2)
    assert body["results"][0]["accepted"] and body["results"][1]["accepted"]
    assert "guardrail" in body["results"][2]["error"]
    assert "duplicate" in body["results"][3]["error"]

    # accepted cases finish in the background — poll until both close
    deadline = time.time() + 120
    final = {"auto_completed", "needs_review", "completed", "rejected"}
    while time.time() < deadline:
        statuses = {
            client.get(f"/api/cases/{cid}").json()["case"]["status"]
            for cid in (good_a["case_id"], good_b["case_id"])
        }
        if statuses <= final:
            break
        time.sleep(1)
    assert statuses <= final, f"bulk cases never finished: {statuses}"


def test_single_ingest_still_returns_final_status(client):
    from datetime import date

    response = client.post(
        "/api/cases/ingest",
        json={
            "discharge_date": date.today().isoformat(),
            "discharge_disposition": "home",
            "primary_diagnosis": "Type 2 diabetes, controlled",
            "has_pcp_on_file": True,
            "payer": "Medicare",
            "referral_specialty": "endocrinology",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "auto_completed"  # sync path unchanged


def test_stats_aggregates_reflect_processed_cases(client):
    client.post("/api/cases", json={"template": "clean"})  # ensure at least one case

    stats = client.get("/api/stats").json()
    assert stats["total_cases"] >= 1
    assert stats["cases_by_status"].get("auto_completed", 0) >= 1
    assert 0.0 <= stats["avg_composite_confidence"] <= 1.0

    by_name = {a["agent_name"]: a for a in stats["agents"]}
    assert set(by_name) == {
        "referral_routing",
        "followup_scheduling",
        "medication_instruction",
        "patient_outreach",
        "discharge_readiness",
        "risk_escalation",
    }
    assert by_name["referral_routing"]["agent_id"] == "AGT-REF-001"
    assert by_name["referral_routing"]["decisions"] >= 1
    assert by_name["referral_routing"]["avg_duration_ms"] is not None


def test_create_high_risk_case_needs_review_then_can_be_approved(client):
    created = client.post("/api/cases", json={"template": "high_risk"}).json()
    case_id = created["case_id"]
    assert created["status"] == "needs_review"

    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["pending_review"] is not None

    review = client.post(
        f"/api/cases/{case_id}/review",
        json={"action": "approved", "reviewer": "test-reviewer", "note": "looks fine"},
    )
    assert review.status_code == 200
    assert review.json()["status"] == "completed"

    detail_after = client.get(f"/api/cases/{case_id}").json()
    assert detail_after["pending_review"] is None
    assert detail_after["case"]["status"] == "completed"


def test_reviewing_a_case_not_pending_returns_409(client):
    response = client.post(
        "/api/cases/no-such-case/review",
        json={"action": "approved", "reviewer": "test-reviewer"},
    )
    assert response.status_code == 409


def test_get_nonexistent_case_returns_404(client):
    response = client.get("/api/cases/no-such-case")
    assert response.status_code == 404


def test_list_cases_includes_created_case(client):
    created = client.post("/api/cases", json={"template": "clean"}).json()
    response = client.get("/api/cases")
    assert response.status_code == 200
    case_ids = {c["case_id"] for c in response.json()}
    assert created["case_id"] in case_ids


def test_ingest_minimal_json_runs_the_full_pipeline(client):
    response = client.post(
        "/api/cases/ingest",
        json={
            "discharge_date": "2026-07-10",
            "discharge_disposition": "home",
            "primary_diagnosis": "Pneumonia, resolved",
            "has_pcp_on_file": True,
            "payer": "Aetna",
            "referral_specialty": "pulmonology",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"].startswith("case-")
    assert body["status"] in {"auto_completed", "needs_review"}

    detail = client.get(f"/api/cases/{body['case_id']}").json()
    assert detail["case"]["payer"] == "Aetna"
    agent_names = {d["agent_name"] for d in detail["agent_decisions"]}
    assert "referral_routing" in agent_names


def test_ingest_accepts_full_fixture_shaped_json_with_extra_fields(client):
    # Same shape as the sample JSON shown in the UI/docs — includes fields
    # (status, created_at, updated_at) that IngestCaseRequest doesn't
    # declare. Pydantic should ignore them, not reject the request.
    case_id = f"case-manual-{uuid4().hex[:8]}"
    response = client.post(
        "/api/cases/ingest",
        json={
            "case_id": case_id,
            "patient_id": "patient-manual-001",
            "admitting_facility": "St. Vincent Medical Center",
            "discharge_date": "2026-07-05",
            "discharge_disposition": "home",
            "primary_diagnosis": "Type 2 diabetes, controlled",
            "has_pcp_on_file": True,
            "payer": "Medicare",
            "referral_specialty": "endocrinology",
            "risk_flags": [],
            "status": "received",
            "source": "synthetic",
            "source_message_id": "synthetic-case-manual",
            "received_at": "2026-07-05T09:00:00Z",
            "created_at": "2026-07-05T09:00:00Z",
            "updated_at": "2026-07-05T09:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["case_id"] == case_id


def test_ingest_missing_required_field_returns_422_with_field_detail(client):
    response = client.post(
        "/api/cases/ingest",
        json={"discharge_date": "2026-07-10", "discharge_disposition": "home"},
    )
    assert response.status_code == 422
    # The single-or-array union nests field errors one level deeper
    # (body → IngestCaseRequest → field), so key off the last segment.
    fields_with_errors = {e["loc"][-1] for e in response.json()["detail"]}
    assert "primary_diagnosis" in fields_with_errors
    assert "has_pcp_on_file" in fields_with_errors
    assert "payer" in fields_with_errors


def test_ingest_duplicate_case_id_returns_409(client):
    payload = {
        "case_id": f"case-dupe-{uuid4().hex[:8]}",
        "discharge_date": "2026-07-10",
        "discharge_disposition": "home",
        "primary_diagnosis": "Pneumonia, resolved",
        "has_pcp_on_file": True,
        "payer": "Aetna",
    }
    first = client.post("/api/cases/ingest", json=payload)
    assert first.status_code == 200

    second = client.post("/api/cases/ingest", json=payload)
    assert second.status_code == 409


def test_audit_filtered_by_case_id(client):
    created = client.post("/api/cases", json={"template": "clean"}).json()
    response = client.get("/api/audit", params={"case_id": created["case_id"]})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 6  # 5 agents + risk_escalation composite
    assert all(r["case_id"] == created["case_id"] for r in rows)
