import pytest
from sqlalchemy.exc import OperationalError

from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.persistence import AgentDecisionRecord, CaseRecord, Database, EventRecord
from carebridge.services.review_gate import HumanReviewGate, fake_human_reviewer
from carebridge.services.router import ConfidenceRouter


@pytest.fixture()
def db():
    database = Database()
    try:
        database.reset_schema()
    except OperationalError:
        pytest.skip("Postgres not reachable — run `docker compose up -d` in backend/")
    yield database


async def test_publishing_writes_case_and_event_rows(db):
    bus = EventBus(db=db)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    with db.Session() as session:
        case_row = session.get(CaseRecord, CASE_A_CLEAN.case_id)
        assert case_row is not None
        assert case_row.status == "received"

        event_rows = session.query(EventRecord).filter_by(case_id=CASE_A_CLEAN.case_id).all()
        assert len(event_rows) == 1
        assert event_rows[0].event_type == "case.created"


async def test_agent_run_writes_a_decision_row(db):
    bus = EventBus(db=db)
    ReferralRoutingAgent(bus)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    with db.Session() as session:
        rows = session.query(AgentDecisionRecord).filter_by(case_id=CASE_A_CLEAN.case_id).all()
        assert len(rows) == 1
        assert rows[0].agent_name == "referral_routing"
        assert rows[0].confidence >= 0.9


async def test_full_pipeline_persists_final_status_for_all_three_fixtures(db):
    bus = EventBus(db=db)
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
        a = session.get(CaseRecord, CASE_A_CLEAN.case_id)
        b = session.get(CaseRecord, CASE_B_PAYER_DELAY.case_id)
        c = session.get(CaseRecord, CASE_C_HIGH_RISK.case_id)

        assert a.status == "auto_completed"
        assert b.status == "completed"  # went to review, then fake-approved
        assert c.status == "completed"

        decisions = session.query(AgentDecisionRecord).all()
        assert len(decisions) == 3  # one per fixture, from the routing agent

        events = session.query(EventRecord).filter_by(case_id=CASE_C_HIGH_RISK.case_id).all()
        event_types = {e.event_type for e in events}
        assert "case.created" in event_types
        assert "case.needs_review" in event_types
        assert "case.review_approved" in event_types
