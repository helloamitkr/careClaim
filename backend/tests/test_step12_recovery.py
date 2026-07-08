"""Step 12 — crash recovery. Simulate a process dying mid-pipeline by wiring
a bus, letting agents write decisions to Postgres, and then throwing that bus
away. A brand-new pipeline (as after a restart) must pick the stranded case
up from the database alone and drive it to a final status."""

import pytest
from sqlalchemy.exc import OperationalError

from carebridge.agents.discharge_readiness import DischargeReadinessAgent
from carebridge.agents.followup_scheduling import FollowUpSchedulingAgent
from carebridge.agents.medication_instruction import MedicationInstructionAgent
from carebridge.agents.patient_outreach import PatientOutreachAgent
from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.agents.risk_escalation import RiskEscalationAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN
from carebridge.persistence import CaseRecord, Database
from carebridge.review_gate import HumanReviewGate
from carebridge.router import ConfidenceRouter
from tests.fakes import FakeLLM


@pytest.fixture()
def db():
    database = Database()
    try:
        database.reset_schema()
    except OperationalError:
        pytest.skip("Postgres not reachable — run `docker compose up -d` in backend/")
    yield database


def _wire_five_source_agents(bus: EventBus) -> None:
    ReferralRoutingAgent(bus)
    FollowUpSchedulingAgent(bus)
    MedicationInstructionAgent(bus, llm=FakeLLM("plain-language instructions"))
    PatientOutreachAgent(bus, llm=FakeLLM("a short friendly message"))
    DischargeReadinessAgent(bus, llm=FakeLLM("READY"))


def _wire_restarted_pipeline(db: Database) -> tuple[EventBus, RiskEscalationAgent]:
    bus = EventBus(db=db)
    _wire_five_source_agents(bus)
    risk_agent = RiskEscalationAgent(bus)
    ConfidenceRouter(bus, listens_to="case.risk_assessed", threshold=0.75)
    HumanReviewGate(bus)
    return bus, risk_agent


async def test_case_with_all_five_decisions_is_completed_from_db_alone(db):
    # Crash scenario: all five agents finished, but the process died before
    # the composite fired — no RiskEscalationAgent on this bus at all.
    crashed_bus = EventBus(db=db)
    _wire_five_source_agents(crashed_bus)
    await crashed_bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    with db.Session() as session:
        assert session.get(CaseRecord, CASE_A_CLEAN.case_id).status == "received"

    # "Restart": fresh bus, empty in-memory state, recovery from Postgres.
    _, risk_agent = _wire_restarted_pipeline(db)
    recovered = await risk_agent.recover_from_db()

    assert recovered == [CASE_A_CLEAN.case_id]
    with db.Session() as session:
        assert session.get(CaseRecord, CASE_A_CLEAN.case_id).status == "auto_completed"


async def test_case_with_partial_decisions_is_rerun_from_scratch(db):
    # Crash scenario: only one agent got its decision recorded.
    crashed_bus = EventBus(db=db)
    ReferralRoutingAgent(crashed_bus)
    await crashed_bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    _, risk_agent = _wire_restarted_pipeline(db)
    recovered = await risk_agent.recover_from_db()

    assert recovered == [CASE_A_CLEAN.case_id]
    with db.Session() as session:
        assert session.get(CaseRecord, CASE_A_CLEAN.case_id).status == "auto_completed"


async def test_recovery_is_a_no_op_when_nothing_is_stranded(db):
    bus, risk_agent = _wire_restarted_pipeline(db)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    with db.Session() as session:
        assert session.get(CaseRecord, CASE_A_CLEAN.case_id).status == "auto_completed"

    assert await risk_agent.recover_from_db() == []


async def test_recovery_without_a_database_is_a_no_op():
    bus = EventBus()  # no db wired at all
    risk_agent = RiskEscalationAgent(bus)
    assert await risk_agent.recover_from_db() == []
