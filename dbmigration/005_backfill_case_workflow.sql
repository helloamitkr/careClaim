-- Migration 005 — backfill case_workflow for cases created before it existed.
--
-- Applied by:  python dbmigration/migrate.py
-- Depends on:  001 (cases, case_workflow)
--
-- Idempotent. Inserts no patient data — only the workflow metadata that every
-- case needs in order to be visible in the doctor/admin panels at all.
--
-- WHY: a case with no case_workflow row was skipped by /api/workflow/queue and
-- rejected by /approve with "case has no workflow record". Cases created by the
-- dashboard's ingest modal, by the fixture endpoint, or by a bulk ingest without
-- `?uploaded_by=` had no row, so they silently vanished from both panels.
--
-- The uploader is genuinely unknown for these, so it is recorded as 'unknown'
-- rather than guessed. An admin can still approve them; sending one back for
-- review requires naming a reviewer explicitly, since there is no uploader to
-- default to.

INSERT INTO public.case_workflow (case_id, uploaded_by, created_at, updated_at)
SELECT c.case_id, 'unknown', c.created_at, c.updated_at
FROM public.cases c
LEFT JOIN public.case_workflow w ON w.case_id = c.case_id
WHERE w.case_id IS NULL
ON CONFLICT (case_id) DO NOTHING;

-- Cases the pipeline already closed were, in effect, approved by the old
-- auto-complete path. Leave approved_at NULL: nothing was ever signed by a
-- human, and back-dating an approval would put unreviewed text in front of a
-- patient. An admin approves them through the panel like any other case.
