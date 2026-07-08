"""Compose a clinician-facing discharge summary draft from agent decisions.

The agents emit machine slugs (`route_to_in_network_cardiology_provider`,
`schedule_followup_2026-07-06`). Those are precise and terrible to read. This
module turns them into prose a clinician can review, edit, and sign.

Deliberately deterministic: no LLM call. The draft must render even when
LLM_AVAILABLE=false, and a clinician editing a summary should not see it change
underneath them between two GETs of the same case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The agents whose output belongs in a discharge summary, in the order a
# clinician reads them. Agents absent from this map are internal (risk scoring,
# routing confidence) and never reach the draft.
_HEADINGS: dict[str, str] = {
    "referral_routing": "Referral",
    "followup_scheduling": "Follow-up",
    "medication_instruction": "Medications",
    "patient_outreach": "Patient outreach",
    "discharge_readiness": "Discharge readiness",
}
_ORDER = list(_HEADINGS)

# The five agents the pipeline runs; a case is draftable once all have reported.
EXPECTED_AGENTS = frozenset(_HEADINGS)


@dataclass(frozen=True)
class Section:
    agent_name: str
    heading: str
    body: str
    confidence: float


def _humanise(decision: str) -> str:
    """`route_to_in_network_cardiology_provider` → `Route to in network
    cardiology provider`. Dates inside the slug are left intact."""
    text = decision.strip()
    # Pull a trailing ISO date out before touching underscores.
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})$", text)
    date = date_match.group(1) if date_match else None
    if date:
        text = text[: date_match.start()].rstrip("_")
    text = text.replace("_", " ").strip()
    if date:
        text = f"{text} on {date}"
    return text[:1].upper() + text[1:] if text else text


def build_sections(decisions) -> list[Section]:
    """`decisions` is any iterable of objects with agent_name / decision /
    confidence / rationale (an ORM row or a stub both work)."""
    by_agent = {d.agent_name: d for d in decisions if d.agent_name in _HEADINGS}
    sections: list[Section] = []
    for agent in _ORDER:
        d = by_agent.get(agent)
        if d is None:
            continue
        body = _humanise(d.decision)
        # The rationale is the agent's reasoning; a clinician reviewing a draft
        # wants it, unlike a patient (see the portal's allowlist projection).
        if d.rationale:
            body = f"{body}. {d.rationale.strip().rstrip('.')}."
        sections.append(
            Section(agent_name=agent, heading=_HEADINGS[agent], body=body, confidence=d.confidence)
        )
    return sections


def compose_draft(case: dict, sections: list[Section]) -> str:
    """The editable narrative. Plain text: the clinician owns it from here."""
    lines = [
        f"Discharge summary — {case.get('patient_id', 'unknown patient')}",
        "",
        f"Primary diagnosis: {case.get('primary_diagnosis', '—')}",
        f"Discharge date: {case.get('discharge_date', '—')}",
        f"Discharged to: {str(case.get('discharge_disposition', '—')).replace('_', ' ')}",
        "",
    ]
    if not sections:
        lines.append("No agent findings yet. The care plan is still being prepared.")
    for section in sections:
        lines.append(f"{section.heading}: {section.body}")
    lines += ["", "Reviewed and edited by the attending clinician before approval."]
    return "\n".join(lines)


def is_ready(decisions) -> bool:
    """A draft is ready once every summary-contributing agent has reported.

    Note this is a property of the decisions, not of case status: a case can sit
    in `needs_review` precisely because it *is* ready and awaiting a human.
    """
    reported = {d.agent_name for d in decisions}
    return EXPECTED_AGENTS.issubset(reported)
