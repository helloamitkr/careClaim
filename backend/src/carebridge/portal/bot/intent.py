"""What the assistant refuses to be asked.

This runs *before* the model sees anything. A system prompt is a request, not a
control: "you are not a doctor, decline clinical questions" is one jailbreak away
from advising a discharged cardiac patient to halve their beta blocker. So the
refusal is a deterministic check in Python, and the refusal text is a constant —
no model generates it.

Deliberately over-broad. A false positive costs a patient one redirect to their
care team, which is the correct destination for a clinical question anyway. A
false negative costs the patient something we cannot take back.

This is a filter, not a classifier. It does not need to understand the question,
only to notice that it is about a body.
"""

from __future__ import annotations

import re

CARE_TEAM_REFUSAL = (
    "I can't help with medical questions — I'm only able to explain the status of "
    "your discharge plan and what happens next. Please contact your care team, and "
    "if this is an emergency, call your local emergency number."
)

# Served when the model returns the OFF_TOPIC sentinel (see answer.py).
#
# Unlike the clinical refusal above, off-topic cannot be caught by pattern — there
# is no regex for "I want to dance", and the set of things a person might say is
# not enumerable. So the model classifies and Python decides what is said. The
# model never authors the reply; that is the half that must not vary.
#
# This is the weaker of the two refusals, and deliberately so: a missed off-topic
# question wastes a patient's time, while a missed clinical question could hurt
# them. The clinical filter therefore stays in Python, ahead of the model.
OFF_TOPIC_REPLY = (
    "I can only help with your care plan — where it stands and what happens next. "
    "For anything else, please contact your care team."
)

# Matched against the lowercased message. Word-boundary anchored so "dose" does
# not fire on "diagnosed" and "med" does not fire on "medium".
_CLINICAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        # Drugs and dosing
        r"\b(dose|dosage|doses|mg|milligrams?|pills?|tablets?|prescriptions?)\b",
        r"\b(medication|medicine|meds|drug|drugs)\b",
        r"\b(take|taking|stop|stopped|skip|skipped|double|halve|increase|decrease)\s+"
        r"\w{0,12}\s?(my|the|this|that)?\s?(med|meds|medication|pill|dose|drug)",
        # Symptoms and self-assessment
        r"\b(symptom|symptoms|pain|hurts?|hurting|bleeding|swelling|swollen|dizzy|"
        r"dizziness|nausea|nauseous|vomit|vomiting|fever|rash|chest pain|"
        r"shortness of breath|can't breathe|cannot breathe|palpitations)\b",
        r"\b(is it (normal|safe|ok|okay)|should i (be )?worried|am i (ok|okay|dying))\b",
        # Requests for advice or diagnosis
        r"\b(should i|can i|is it safe to|what should i do about|do i need to)\b.{0,40}"
        r"\b(take|stop|eat|drink|exercise|drive|lift|work|surgery|doctor|hospital|er)\b",
        r"\b(diagnose|diagnosis|prognosis|treat|treatment|cure|side effects?)\b",
        # Emergencies — never route these through a language model
        r"\b(emergency|911|999|112|ambulance|suicide|kill myself|overdose)\b",
    )
)


def is_clinical_question(message: str) -> bool:
    """True when the message asks about the patient's body rather than their case."""
    text = message.lower()
    return any(pattern.search(text) for pattern in _CLINICAL_PATTERNS)


# An empty or absurd message never reaches the model either.
MAX_MESSAGE_CHARS = 500


def validate(message: str) -> str:
    """Returns the cleaned message. Raises ValueError for anything unusable."""
    cleaned = message.strip()
    if not cleaned:
        raise ValueError("Ask me something about your care plan.")
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise ValueError(f"Please keep your question under {MAX_MESSAGE_CHARS} characters.")
    return cleaned
