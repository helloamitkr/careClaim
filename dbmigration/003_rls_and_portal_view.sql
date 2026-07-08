-- Migration 003 — row-level security and the portal read model.
-- Phase 3 + Phase 4 of PATIENT_PORTAL_DESIGN.md.
--
-- Applied by:  python dbmigration/migrate.py
-- Depends on:  001 (cases, case_workflow) and 002 (the roles it grants to)
--
-- Also re-applied automatically by Database.init_schema() after create_all(),
-- because DROP TABLE takes the policies and the view down with it and a test
-- that calls reset_schema() would otherwise silently disable RLS.
--
-- Idempotent. Inserts no data.
--
-- HOW THE ENFORCEMENT WORKS
--
--   portal role  --SELECT-->  portal.portal_case_view   (owner: carebridge_owner)
--                                      |
--                                      | executes as its owner, so PostgreSQL
--                                      | applies the *owner's* RLS policies
--                                      v
--                              public.cases  (RLS enabled)
--                                      |
--                          policy: patient_id = current_setting('app.patient_id')
--
-- The portal role has no privilege on public.cases whatsoever. Even if the view
-- were replaced with SELECT *, the RLS policy still bounds it to one patient.
-- And if the policy were dropped, the portal role still cannot reach the table.
-- Two independent controls, failing in different ways.
--
-- current_setting(..., true) returns NULL when unset, and `patient_id = NULL`
-- matches no rows. Forgetting to set it therefore fails closed.

-- ---------------------------------------------------------------------------
-- RLS on the clinical table
-- ---------------------------------------------------------------------------
ALTER TABLE public.cases ENABLE ROW LEVEL SECURITY;

-- The pipeline and staff API must see every case.
DROP POLICY IF EXISTS app_full_access ON public.cases;
CREATE POLICY app_full_access ON public.cases
    FOR ALL TO carebridge_app
    USING (true) WITH CHECK (true);

-- The view owner — and therefore anything reading through the view — is
-- confined to the patient named by the current transaction.
DROP POLICY IF EXISTS portal_own_rows ON public.cases;
CREATE POLICY portal_own_rows ON public.cases
    FOR SELECT TO carebridge_owner
    USING (patient_id = current_setting('app.patient_id', true));

-- The view owner needs base-table SELECT; RLS above is what bounds it.
GRANT SELECT ON public.cases TO carebridge_owner;
-- Joined into the view for `approved_summary`. No RLS needed here: the join to
-- `cases` is already row-filtered, so only the patient's own workflow row can
-- surface. The portal role still has no direct privilege on this table.
GRANT SELECT ON public.case_workflow TO carebridge_owner;

-- ---------------------------------------------------------------------------
-- The read model: an allowlist projection, not `SELECT *`.
--
-- Absent on purpose (§7 of the design): payer, admitting_facility,
-- source_message_id, source, risk_flags, confidence, rationale, and the raw
-- `snapshot` blob — which would silently leak every column added in future.
-- ---------------------------------------------------------------------------
-- `approved_summary` is NULL until an admin signs the case off. Patient
-- visibility is a consequence of approval, never of the agents finishing —
-- expressed here as a CASE, so no application bug can leak an unapproved draft.
DROP VIEW IF EXISTS portal.portal_case_view;
CREATE VIEW portal.portal_case_view AS
    SELECT
        c.case_id,
        c.patient_id,
        c.status                                       AS internal_status,
        c.snapshot ->> 'primary_diagnosis'             AS primary_diagnosis,
        c.snapshot ->> 'discharge_date'                AS discharge_date,
        c.snapshot ->> 'discharge_disposition'         AS discharge_disposition,
        CASE WHEN w.approved_at IS NOT NULL THEN w.summary_text END AS approved_summary,
        w.approved_at,
        c.updated_at
    FROM public.cases c
    LEFT JOIN public.case_workflow w ON w.case_id = c.case_id;

ALTER VIEW portal.portal_case_view OWNER TO carebridge_owner;
GRANT SELECT ON portal.portal_case_view TO carebridge_portal;

-- Staff API reads cases directly, not through this view.
GRANT SELECT ON portal.portal_case_view TO carebridge_app;
