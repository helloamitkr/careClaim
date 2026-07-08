"""The patient-facing status assistant.

A patient whose plan is held up sees "Please contact your care team." and nothing
else. The real reason lives in `agent_decisions.rationale` — their own record,
written for clinicians. This package translates it.

    chat/
      context.py  RLS-bounded read of the case's agent decisions (migration 006)
      intent.py   deterministic refusal of clinical questions, before the model
      answer.py   the one LLM call: translate the blockers, invent nothing
      redact.py   discard any reply carrying internal vocabulary

Four controls, in the order a request meets them:

  1. The route takes no patient_id; the session supplies it (as everywhere else).
  2. `intent.is_clinical_question` refuses medical questions in Python. A system
     prompt is a request; this is a control.
  3. The context is bounded by row-level security before the model sees it, so
     the worst a prompt injection can reach is the patient's own record.
  4. `redact.sanitize` discards a reply that quotes agent names or confidence
     scores, rather than trying to repair it.

Every message writes a phi_access_log row. The assistant is a PHI read.
"""
