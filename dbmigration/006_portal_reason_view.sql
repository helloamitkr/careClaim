-- Migration 006 — the read model behind the patient chat assistant.
--
-- Applied by:  python dbmigration/migrate.py
-- Depends on:  001 (cases, agent_decisions), 002 (roles), 003 (RLS on cases)
--
-- Idempotent. Inserts no data.
--
-- WHY THIS EXISTS
--
-- A patient asking "why is my plan held up?" can only be answered from
-- agent_decisions.rationale — a column the portal_case_view deliberately omits,
-- because a raw rationale ("composite confidence set by weakest signal(s) —
-- discharge_readiness (0.30)") is meaningless and alarming to a patient. It is
-- still their own record: HIPAA's minimum-necessary standard does not apply to
-- disclosures to the individual (§164.502(b)(2)(i)). So the constraint is not
-- "never show them" but "never show them raw" — which is the chat layer's job,
-- not this view's.
--
-- HOW IT STAYS SAFE
--
--   portal role --SELECT--> portal.portal_case_reason_view  (owner: carebridge_owner)
--                                     |
--                                     | not security_invoker, so it executes as
--                                     | its owner and PostgreSQL applies the
--                                     | *owner's* RLS policies
--                                     v
--                      public.agent_decisions JOIN public.cases (RLS enabled)
--
-- agent_decisions has no RLS of its own and does not need any: the join to
-- `cases` is what bounds it. carebridge_owner's policy on `cases` restricts that
-- side to current_setting('app.patient_id'), so the join can only yield decisions
-- for cases that patient owns. With the setting unset, current_setting(...,true)
-- is NULL, `patient_id = NULL` matches nothing, and the view returns zero rows.
-- Fails closed, exactly like portal_case_view.
--
-- The portal role still has no privilege on either base table.

-- ---------------------------------------------------------------------------
-- The owner needs to read the decisions in order to join them.
-- ---------------------------------------------------------------------------
GRANT SELECT ON public.agent_decisions TO carebridge_owner;

-- ---------------------------------------------------------------------------
-- The view. Note what is absent: `id`, and any column added to agent_decisions
-- later. Enumerated on purpose — SELECT * would leak the next one.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS portal.portal_case_reason_view;

CREATE VIEW portal.portal_case_reason_view AS
SELECT
    d.case_id,
    c.patient_id,
    c.status        AS internal_status,
    d.agent_name,
    d.decision,
    d.confidence,
    d.rationale
FROM public.agent_decisions d
JOIN public.cases c ON c.case_id = d.case_id;

ALTER VIEW portal.portal_case_reason_view OWNER TO carebridge_owner;

GRANT SELECT ON portal.portal_case_reason_view TO carebridge_portal;
