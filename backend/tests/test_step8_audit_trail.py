import pytest
from sqlalchemy.exc import OperationalError

from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.audit import AuditLogRecord, AuditTrail
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.persistence import Database
from carebridge.review_gate import HumanReviewGate, fake_human_reviewer
from carebridge.router import ConfidenceRouter


@pytest.fixture()
def db():
    database = Database()
    try:
        database.reset_schema()
    except OperationalError:
        pytest.skip("Postgres not reachable — run `docker compose up -d` in backend/")
    yield database


@pytest.fixture()
def audit(db):
    return AuditTrail(db)


async def test_agent_run_writes_an_append_only_audit_row(db, audit):
    bus = EventBus(db=db, audit=audit)
    ReferralRoutingAgent(bus)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    with db.Session() as session:
        rows = session.query(AuditLogRecord).filter_by(case_id=CASE_A_CLEAN.case_id).all()
        assert len(rows) == 1
        assert rows[0].agent_id == "referral_routing"
        assert rows[0].confidence == 0.95
        assert rows[0].reviewer is None
        assert CASE_A_CLEAN.case_id in rows[0].input_summary


async def test_human_review_writes_an_audit_row_with_reviewer(db, audit):
    bus = EventBus(db=db, audit=audit)
    ReferralRoutingAgent(bus)
    ConfidenceRouter(bus, listens_to="referral.routed", threshold=0.75)
    gate = HumanReviewGate(bus)

    await bus.publish(Event(event_type="case.created", case=CASE_C_HIGH_RISK))
    review = gate.pending[CASE_C_HIGH_RISK.case_id]
    status, note = fake_human_reviewer(review)
    await gate.act(CASE_C_HIGH_RISK.case_id, status, reviewer="demo-reviewer", note=note)

    with db.Session() as session:
        rows = session.query(AuditLogRecord).filter_by(case_id=CASE_C_HIGH_RISK.case_id).all()
        agent_ids = {r.agent_id for r in rows}
        assert agent_ids == {"referral_routing", "human_review_gate"}

        review_row = next(r for r in rows if r.agent_id == "human_review_gate")
        assert review_row.reviewer == "demo-reviewer"
        assert review_row.decision == "approved"
        assert review_row.confidence == 0.25  # carried over from the proposed decision


async def test_full_pipeline_produces_expected_audit_row_count(db, audit):
    bus = EventBus(db=db, audit=audit)
    ReferralRoutingAgent(bus)
    ConfidenceRouter(bus, listens_to="referral.routed", threshold=0.75)
    gate = HumanReviewGate(bus)

    for case in (CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK):
        await bus.publish(Event(event_type="case.created", case=case))

    for case_id in list(gate.pending.keys()):
        review = gate.pending[case_id]
        status, note = fake_human_reviewer(review)
        await gate.act(case_id, status, reviewer="demo-reviewer", note=note)

    with db.Session() as session:
        rows = session.query(AuditLogRecord).all()
        # 3 agent runs (one per fixture) + 2 human reviews (case-B, case-C)
        assert len(rows) == 5
        assert all(r.rationale for r in rows)  # every row explains itself
