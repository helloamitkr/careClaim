from datetime import date, timedelta

from carebridge.agents.base import AgentDecision
from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN
from carebridge.guardrails import (
    REDACTED,
    InputGuardrail,
    apply_output_guardrail,
    scrub_pii,
)

# --- PII scrubbing ---------------------------------------------------------


def test_scrub_pii_redacts_ssn_phone_email_mrn():
    text = "pt 123-45-6789, call 555-867-5309, mail j.doe@example.com, MRN: 8675309"
    scrubbed, found = scrub_pii(text)
    assert scrubbed.count(REDACTED) == 4
    assert set(found) == {"ssn", "phone", "email", "mrn"}
    for raw in ("123-45-6789", "555-867-5309", "j.doe@example.com", "8675309"):
        assert raw not in scrubbed


def test_scrub_pii_leaves_clean_text_alone():
    text = "Type 2 diabetes, controlled"
    scrubbed, found = scrub_pii(text)
    assert scrubbed == text
    assert found == []


# --- input guardrail -------------------------------------------------------


def _recent_case(**overrides):
    updates = {"discharge_date": date.today(), **overrides}
    return CASE_A_CLEAN.model_copy(update=updates)


def test_clean_case_passes_unchanged():
    case = _recent_case()
    report = InputGuardrail().check(case)
    assert report.passed
    assert report.violations == []
    assert report.redactions == []
    assert report.case == case


def test_far_future_discharge_date_is_rejected():
    case = _recent_case(discharge_date=date.today() + timedelta(days=90))
    report = InputGuardrail().check(case)
    assert not report.passed
    assert any("future" in v for v in report.violations)
    assert report.case is None


def test_stale_discharge_date_is_rejected():
    case = _recent_case(discharge_date=date.today() - timedelta(days=400))
    report = InputGuardrail().check(case)
    assert not report.passed
    assert any("past" in v for v in report.violations)


def test_blank_diagnosis_is_rejected():
    case = _recent_case(primary_diagnosis="   ")
    report = InputGuardrail().check(case)
    assert not report.passed
    assert "primary_diagnosis is blank" in report.violations


def test_pii_in_free_text_is_scrubbed_not_rejected():
    case = _recent_case(
        primary_diagnosis="CHF, contact 555-867-5309",
        risk_flags=["fall_risk", "caregiver email j.doe@example.com"],
    )
    report = InputGuardrail().check(case)
    assert report.passed  # scrub, don't reject — the clinical content is still useful
    assert "primary_diagnosis:phone" in report.redactions
    assert "risk_flags[1]:email" in report.redactions
    assert "555-867-5309" not in report.case.primary_diagnosis
    assert "j.doe@example.com" not in report.case.risk_flags[1]
    assert report.case.risk_flags[0] == "fall_risk"  # untouched


# --- output guardrail ------------------------------------------------------


def test_confidence_is_clamped_into_unit_range():
    over = AgentDecision(agent_name="x", decision="d", confidence=1.7, rationale="r")
    under = AgentDecision(agent_name="x", decision="d", confidence=-0.2, rationale="r")
    assert apply_output_guardrail(over).confidence == 1.0
    assert apply_output_guardrail(under).confidence == 0.0


def test_pii_is_scrubbed_from_rationale():
    decision = AgentDecision(
        agent_name="x", decision="d", confidence=0.9, rationale="routed, pt SSN 123-45-6789"
    )
    assert "123-45-6789" not in apply_output_guardrail(decision).rationale


def test_empty_rationale_is_replaced():
    decision = AgentDecision(agent_name="x", decision="d", confidence=0.9, rationale="  ")
    assert apply_output_guardrail(decision).rationale.strip() != ""


def test_clean_decision_passes_through_identical():
    decision = AgentDecision(agent_name="x", decision="d", confidence=0.9, rationale="fine")
    assert apply_output_guardrail(decision) is decision


# --- wired into the pipeline ------------------------------------------------


async def test_agent_output_on_the_bus_has_been_guarded():
    """Every event payload downstream of an agent must already be sanitized —
    the guardrail runs inside Agent.run, not as an optional extra step."""

    class LeakyAgent(ReferralRoutingAgent):
        def _decide(self, case):
            return AgentDecision(
                agent_name=self.name,
                decision="route",
                confidence=2.0,
                rationale="pt phone 555-867-5309",
            )

    bus = EventBus()
    LeakyAgent(bus)
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("referral.routed", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    decision = received[0].payload["decision"]
    assert decision["confidence"] == 1.0
    assert "555-867-5309" not in decision["rationale"]
