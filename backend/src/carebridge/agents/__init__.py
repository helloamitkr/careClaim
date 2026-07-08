"""The six pipeline agents.

Membership here is a contract, not a naming convention. An `Agent`:

  1. subscribes to the bus on construction (`bus.subscribe(self.listens_to, ...)`),
  2. persists an `AgentDecision` row on every run, and
  3. holds `bus.db` — the app engine, full privileges, no row-level security.

Three of the six call an LLM (medication_instruction, patient_outreach,
discharge_readiness). The other three — referral_routing, followup_scheduling,
risk_escalation — are lookup tables and arithmetic, deliberately: a payer-network
match and a weakest-signal calculation have correct answers, and a model can only
make them wrong.

NOT AN AGENT: the patient chat bot (`carebridge/portal/bot/`)

It is request/response, not an event. Were it to satisfy contract 2, its decision
rows would be re-read as clinical rationale by `portal.portal_case_reason_view`,
and its own answers would become the context for the next question. And contract 3
is exactly what it must not have: it reads as `carebridge_portal` through
`portal_engine()`, so a prompt injection reaches only the asking patient's record.

It lives in the portal because it is a patient-facing read, not a clinical
decision-maker. Different trust zone, different database role.
"""
