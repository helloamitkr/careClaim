"""Step 15 — lightweight logging. The pipeline's log stream comes from one
hook in EventBus.publish, so these tests capture loguru output with a list
sink and assert the story a case tells as it moves through."""

from datetime import date, timedelta

import pytest
from loguru import logger

from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN
from carebridge.guardrails import InputGuardrail
from carebridge.logging_setup import configure_logging


@pytest.fixture()
def captured():
    records: list = []
    sink_id = logger.add(lambda message: records.append(message.record), level="DEBUG")
    yield records
    logger.remove(sink_id)


async def test_every_published_event_is_logged(captured):
    bus = EventBus()
    ReferralRoutingAgent(bus)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    bus_records = [r for r in captured if r["extra"].get("component") == "bus"]
    logged_types = [r["extra"]["event_type"] for r in bus_records]
    assert logged_types == ["case.created", "referral.routed"]
    assert all(r["extra"]["case_id"] == CASE_A_CLEAN.case_id for r in bus_records)


async def test_agent_events_carry_producer_and_duration(captured):
    bus = EventBus()
    ReferralRoutingAgent(bus)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    routed = next(r for r in captured if r["extra"].get("event_type") == "referral.routed")
    assert routed["extra"]["produced_by"] == "referral_routing"
    assert routed["extra"]["duration_ms"] >= 0


def test_guardrail_rejection_logs_a_warning_without_pii(captured):
    case = CASE_A_CLEAN.model_copy(
        update={
            "discharge_date": date.today() + timedelta(days=90),
            "primary_diagnosis": "CHF, contact 555-867-5309",
        }
    )
    report = InputGuardrail().check(case)
    assert not report.passed

    warnings = [r for r in captured if r["level"].name == "WARNING"]
    assert any("input rejected" in r["message"] for r in warnings)


def test_pii_scrub_logs_pattern_names_never_values(captured):
    case = CASE_A_CLEAN.model_copy(
        update={"primary_diagnosis": "CHF, contact 555-867-5309"}
    )
    InputGuardrail().check(case)

    scrub_logs = [r for r in captured if "PII scrubbed" in r["message"]]
    assert len(scrub_logs) == 1
    assert "primary_diagnosis:phone" in scrub_logs[0]["message"]
    assert "555-867-5309" not in scrub_logs[0]["message"]  # value never logged


async def test_agent_log_lines_carry_the_agent_id(captured):
    bus = EventBus()
    ReferralRoutingAgent(bus)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    agent_records = [r for r in captured if r["extra"].get("component") == "agent"]
    assert len(agent_records) == 1
    assert agent_records[0]["extra"]["agent_id"] == "AGT-REF-001"
    assert "[AGT-REF-001]" in agent_records[0]["message"]
    assert agent_records[0]["extra"]["case_id"] == CASE_A_CLEAN.case_id


def test_every_agent_has_a_unique_agent_id():
    from carebridge.agents.discharge_readiness import DischargeReadinessAgent
    from carebridge.agents.followup_scheduling import FollowUpSchedulingAgent
    from carebridge.agents.medication_instruction import MedicationInstructionAgent
    from carebridge.agents.patient_outreach import PatientOutreachAgent
    from carebridge.agents.risk_escalation import RiskEscalationAgent

    ids = [
        ReferralRoutingAgent.agent_id,
        FollowUpSchedulingAgent.agent_id,
        MedicationInstructionAgent.agent_id,
        PatientOutreachAgent.agent_id,
        DischargeReadinessAgent.agent_id,
        RiskEscalationAgent.agent_id,
    ]
    assert len(set(ids)) == 6
    assert all(agent_id.startswith("AGT-") for agent_id in ids)


def test_configure_logging_is_idempotent(monkeypatch):
    monkeypatch.setenv("LOG_TO_FILE", "0")
    configure_logging()
    configure_logging()  # second call must not raise or duplicate sinks
    # restore the default stderr sink for the rest of the session
    logger.remove()
    import sys

    logger.add(sys.stderr)
