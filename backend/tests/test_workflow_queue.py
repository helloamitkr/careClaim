"""The clinician queue exposes the approved summary — and only the approved one.

`case_workflow.summary_text` holds two different things over a case's life:

  1. after `submit_review`  — a doctor's *proposed* edit, not yet signed
  2. after `approve`        — the frozen text the patient is now reading

The queue row is badged with the stage, so returning (1) would render unapproved
text under a row a reader scans as final. Only (2) may cross the wire.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from carebridge.api.main import app
from carebridge.models import CaseStatus
from carebridge.persistence import CaseRecord, Database
from carebridge.services import workflow


@pytest.fixture(scope="module")
def db():
    database = Database()
    try:
        database.init_schema()
    except OperationalError:
        pytest.skip("Postgres not reachable")
    return database


@pytest.fixture
def case(db):
    """A bare case with a workflow row. No agents run — the queue does not need
    them, and `agents_ready` is allowed to be False here."""
    case_id = f"case-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    with db.Session() as session:
        session.add(
            CaseRecord(
                case_id=case_id,
                patient_id="pt-queue-test",
                status=CaseStatus.COMPLETED.value,
                snapshot={"primary_diagnosis": "Test Condition"},
                created_at=now,
                updated_at=now,
            )
        )
        workflow.claim(session, case_id, "dr.uploader")
        session.commit()

    yield case_id

    with db.Session() as session:
        session.query(workflow.CaseWorkflow).filter_by(case_id=case_id).delete()
        session.query(CaseRecord).filter_by(case_id=case_id).delete()
        session.commit()


def _row(client, case_id: str) -> dict:
    rows = client.get("/api/workflow/queue", params={"role": "admin"}).json()
    return next(r for r in rows if r["case_id"] == case_id)


def test_summary_is_absent_before_anyone_approves(db, case):
    with TestClient(app) as client:
        assert _row(client, case)["summary_text"] is None


def test_a_doctors_unapproved_edit_never_reaches_the_queue(db, case):
    """submit_review parks the doctor's text in the same column approve uses."""
    actor = workflow.Actor(username="admin-1", role="admin")
    with db.Session() as session:
        workflow.request_review(session, case, actor, "dr.uploader", "please check")
        workflow.submit_review(
            session, case, workflow.Actor("dr.uploader", "doctor"), "DRAFT — do not release"
        )
        session.commit()

    with TestClient(app) as client:
        row = _row(client, case)
    assert row["stage"] != "approved"
    assert row["approved_at"] is None
    assert row["summary_text"] is None  # the text exists in the DB; it is not approved


def test_the_approved_summary_is_returned_to_clinicians(db, case):
    with db.Session() as session:
        workflow.approve(session, case, workflow.Actor("admin-1", "admin"), "Signed summary.")
        session.commit()

    with TestClient(app) as client:
        row = _row(client, case)
    assert row["stage"] == "approved"
    assert row["summary_text"] == "Signed summary."
    assert row["approved_by"] == "admin-1"
    assert row["approved_at"] is not None
