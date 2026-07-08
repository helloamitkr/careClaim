"""The last thing that touches a reply before a patient reads it.

`answer.py` puts raw agent rationale into the prompt. A model asked to paraphrase
will sometimes quote instead — echoing "discharge_readiness (0.30)" or
"composite confidence set by weakest signal(s)". That is not a leak in the HIPAA
sense (it is the patient's own record) but it is meaningless and alarming, which
is its own kind of harm.

So the reply is checked against the internal vocabulary. On a hit we do not try
to repair it — a partially-scrubbed sentence about the patient's own care is
worse than a clean fallback. We discard the reply and return the fallback text.

Fails closed: unknown model output is treated as unsafe, not as fine.
"""

from __future__ import annotations

import re

from loguru import logger

from carebridge.guardrails import scrub_pii

FALLBACK = (
    "Your care team is still finalising part of your plan. They'll be in touch, "
    "and you can contact them any time if you'd like an update."
)

# Every agent's internal name. The patient has no idea what a "referral_routing"
# is, and telling them teaches them nothing.
_AGENT_NAMES = (
    "referral_routing",
    "followup_scheduling",
    "medication_instruction",
    "patient_outreach",
    "discharge_readiness",
    "risk_escalation",
)

_BANNED: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Agent identifiers, snake_case or spaced.
        *(rf"\b{name}\b" for name in _AGENT_NAMES),
        *(rf"\b{name.replace('_', ' ')}\b" for name in _AGENT_NAMES),
        # Machine-confidence vocabulary and the numbers that go with it.
        r"\bconfidence\b",
        r"\bcomposite\b",
        r"\b0\.\d{1,2}\b",
        r"\b\d{1,3}\s?%\s?(confident|confidence)\b",
        # The pipeline's own status strings.
        r"\b(auto_completed|needs_review|in_progress|internal status)\b",
        # Anything that reveals there are agents at all. Naming the vendors too:
        # a reply that says "Claude reviewed your case" is its own disclosure.
        r"\b(ai|a\.i\.|llm|gpt|claude|gemini|ollama|language model|chat ?bot)\b",
        r"\b(the|our|an?|this)\s+(ai\s+|ml\s+)?(model|algorithm|agents?)\b",
        r"\bagents?\s+(said|decided|flagged|scored|reviewed)\b",
        # "your insurance agent" is deliberately still allowed — the determiner
        # list above excludes "your", because that is a person, not a component.
        # A model that starts explaining its instructions has been injected.
        r"\b(system prompt|my instructions|ignore (the|all|previous))\b",
    )
)


def violations(reply: str) -> list[str]:
    """Which internal terms leaked. Returns the *patterns* that fired, never the
    matched text — logging the leak is another copy of the leak."""
    return [p.pattern for p in _BANNED if p.search(reply)]


def sanitize(reply: str, *, case_id: str) -> str:
    """Return a reply safe to show, or FALLBACK if the model's was not.

    Also scrubs PII: the rationale that fed the prompt came from doctor-uploaded
    free text, and an MRN that survived the input guardrail must not survive this.
    """
    cleaned, redactions = scrub_pii(reply.strip())

    if redactions:
        logger.bind(component="portal.bot", case_id=case_id).warning(
            "PII scrubbed from an assistant reply: {names}", names=", ".join(redactions)
        )

    if not cleaned:
        return FALLBACK

    leaked = violations(cleaned)
    if leaked:
        logger.bind(component="portal.bot", case_id=case_id).warning(
            "assistant reply discarded — internal vocabulary leaked: {patterns}",
            patterns="; ".join(leaked),
        )
        return FALLBACK

    return cleaned
