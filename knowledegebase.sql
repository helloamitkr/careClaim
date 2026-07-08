-- ============================================================================
-- CareBridge AI — Discharge Workflow Knowledge Base (RAG seed example)
-- ============================================================================
--
-- WHAT THIS IS
--   One Postgres table that acts as the retrieval layer ("RAG store") for all
--   five agents. Each agent retrieves grounded clinical/administrative content
--   by exact key instead of relying on what the LLM remembers — the LLM only
--   rephrases retrieved content, it never invents it.
--
-- WHO READS WHAT
--   Agent                  category      lookup_key convention
--   ---------------------  ------------  --------------------------------------
--   Medication Instruction 'medication'  primary_diagnosis        e.g. 'Stroke'
--   Follow-up Scheduling   'followup'    retrieved by the `specialty` column
--                                        (lookup_key holds the diagnosis the
--                                        rule was authored for)
--   Referral Routing       'insurance'   payer:specialty          e.g. 'Cigna:cardiology'
--                                        payer:*  = payer covers every specialty
--   Discharge Readiness    'policy'      primary_diagnosis        e.g. 'Stroke'
--   Risk Escalation        'risk'        risk_flag                e.g. 'fall_risk'
--   Patient Outreach       'outreach'    primary_diagnosis        e.g. 'Stroke'
--
-- RETRIEVAL CONTRACT (what agent code must do)
--   1. Filter is_active = TRUE — retired guidance must never be retrieved.
--   2. Order by priority ASC, version DESC and take the first row — priority
--      breaks ties between overlapping rows (lower number wins), version picks
--      the newest revision of the same guidance.
--   3. No match = low-confidence path (e.g. medication_instructions_unavailable),
--      never a made-up answer.
--
-- UPDATING CONTENT (clinical governance)
--   Never UPDATE content in place and never DELETE. Insert a new row with
--   version = old + 1, then set is_active = FALSE on the old row. History is
--   an audit trail: "what did the system believe on the day it advised
--   patient X" must stay answerable.
--
-- LOADING
--   psql postgresql://carebridge:carebridge@localhost:5432/carebridge -f knowledegebase.sql
--   (idempotent — drops and recreates the table with seed data)
--
-- All content below is SYNTHETIC demo data, not real clinical guidance.
-- ============================================================================

DROP TABLE IF EXISTS knowledge_base;

CREATE TABLE knowledge_base (
    -- identity -----------------------------------------------------------
    id              SERIAL PRIMARY KEY,
    category        VARCHAR(50)  NOT NULL
                    CHECK (category IN ('medication','followup','insurance',
                                        'policy','risk','outreach')),
    lookup_key      VARCHAR(150) NOT NULL,

    -- content ------------------------------------------------------------
    title           VARCHAR(200),
    content         TEXT NOT NULL,

    -- provenance: where this guidance came from, so a reviewer can trace
    -- any agent statement back to an authoritative document
    source          VARCHAR(200),
    specialty       VARCHAR(100),
    tags            TEXT[]       DEFAULT '{}',

    -- retrieval control ---------------------------------------------------
    priority        SMALLINT     NOT NULL DEFAULT 100,  -- lower wins on ties
    version         INTEGER      NOT NULL DEFAULT 1,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,

    -- clinical governance: content expires and must be re-reviewed --------
    effective_date  DATE         NOT NULL DEFAULT CURRENT_DATE,
    review_by_date  DATE,
    approved_by     VARCHAR(100),

    -- structured extras the content sentence can't hold (numbers agents
    -- can act on without parsing prose), e.g. {"followup_days": 7}
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- one row per revision of the same guidance
    UNIQUE (category, lookup_key, version)
);

-- The hot path: every agent lookup hits this. Partial index — retired rows
-- are dead weight the retrieval query is not allowed to see anyway.
CREATE INDEX idx_kb_active_lookup
    ON knowledge_base (category, lookup_key)
    WHERE is_active;

-- Keyword retrieval aid ("find everything tagged cardiology").
CREATE INDEX idx_kb_tags ON knowledge_base USING GIN (tags);

-- Structured-field queries, e.g. metadata->>'action' = 'REJECT'.
CREATE INDEX idx_kb_metadata ON knowledge_base USING GIN (metadata);

COMMENT ON TABLE  knowledge_base                IS 'Retrieval store for all agents — grounded content the LLM rephrases but never invents. Synthetic demo data.';
COMMENT ON COLUMN knowledge_base.category       IS 'Which agent this row serves: medication | followup | insurance | policy | risk | outreach';
COMMENT ON COLUMN knowledge_base.lookup_key     IS 'Exact-match retrieval key. Diagnosis for most categories; payer:specialty (or payer:*) for insurance; risk flag for risk.';
COMMENT ON COLUMN knowledge_base.content        IS 'The retrievable guidance, written as patient/clinician-facing prose.';
COMMENT ON COLUMN knowledge_base.source         IS 'Provenance — the protocol/contract/policy document this row was authored from.';
COMMENT ON COLUMN knowledge_base.specialty      IS 'Clinical specialty the row belongs to, for filtering and reporting.';
COMMENT ON COLUMN knowledge_base.tags           IS 'Free-form keywords to aid discovery and future semantic search.';
COMMENT ON COLUMN knowledge_base.priority       IS 'Tie-break when several rows match one lookup: lower number wins.';
COMMENT ON COLUMN knowledge_base.version        IS 'Revision number. Update = insert version+1 and deactivate the old row; never edit in place.';
COMMENT ON COLUMN knowledge_base.is_active      IS 'FALSE = retired. Retrieval queries must filter on TRUE; old rows are audit history.';
COMMENT ON COLUMN knowledge_base.effective_date IS 'Date this guidance took effect.';
COMMENT ON COLUMN knowledge_base.review_by_date IS 'Clinical content expires — flag rows past this date for re-review.';
COMMENT ON COLUMN knowledge_base.approved_by    IS 'Governance sign-off: who approved this content for use.';
COMMENT ON COLUMN knowledge_base.metadata       IS 'Structured extras agents can act on without parsing prose, e.g. {"followup_days": 7}, {"action": "NEEDS_REVIEW"}.';

-- ============================================================================
-- Medication Guidelines — read by the Medication Instruction agent
-- metadata: warning_signs the outreach/readiness agents can cross-check
-- ============================================================================

INSERT INTO knowledge_base
(category, lookup_key, title, content, source, specialty, tags, priority, effective_date, review_by_date, approved_by, metadata)
VALUES

('medication','Post Appendectomy','Post Appendectomy Medication',
'Take prescribed pain medication as directed. Keep the incision clean and dry. Avoid lifting heavy objects for two weeks. Seek medical attention if you develop fever, redness, swelling, or drainage.',
'Internal Discharge Protocol DP-102 (synthetic)','general_surgery',
ARRAY['post-op','pain-management','wound-care'],100,'2026-01-01','2027-01-01','Dr. A. Reviewer (demo)',
'{"activity_restriction_weeks": 2, "warning_signs": ["fever","redness","swelling","drainage"]}'),

('medication','Heart Failure Exacerbation','Heart Failure Medication',
'Take Furosemide every morning. Weigh yourself daily. Limit sodium intake. Call your provider if you gain more than 3 pounds in one day.',
'Internal Discharge Protocol DP-114 (synthetic)','cardiology',
ARRAY['chf','diuretic','daily-weight'],100,'2026-01-01','2027-01-01','Dr. A. Reviewer (demo)',
'{"drug": "Furosemide", "weight_gain_alert_lbs": 3, "diet": "low_sodium"}'),

('medication','Stroke','Stroke Medication',
'Take all prescribed medications exactly as directed. Monitor blood pressure daily. Do not stop blood thinners unless instructed.',
'Internal Discharge Protocol DP-121 (synthetic)','neurology',
ARRAY['stroke','anticoagulant','blood-pressure'],100,'2026-01-01','2027-01-01','Dr. A. Reviewer (demo)',
'{"critical_class": "anticoagulant", "monitor": "blood_pressure_daily"}'),

('medication','Type 2 Diabetes','Diabetes Medication',
'Take Metformin with meals. Check blood sugar every morning before breakfast. Follow your diabetic diet.',
'Internal Discharge Protocol DP-130 (synthetic)','endocrinology',
ARRAY['diabetes','metformin','glucose-monitoring'],100,'2026-01-01','2027-01-01','Dr. A. Reviewer (demo)',
'{"drug": "Metformin", "with_food": true, "monitor": "fasting_glucose_daily"}'),

('medication','COPD','COPD Medication',
'Use inhalers exactly as prescribed. Continue oxygen therapy if ordered. Seek care immediately if breathing worsens.',
'Internal Discharge Protocol DP-138 (synthetic)','pulmonology',
ARRAY['copd','inhaler','oxygen'],100,'2026-01-01','2027-01-01','Dr. A. Reviewer (demo)',
'{"warning_signs": ["worsening_breathing"], "oxygen_dependent_possible": true}'),

('medication','Pneumonia, resolved','Pneumonia Medication',
'Complete the full antibiotic course if prescribed. Drink plenty of fluids and monitor for fever or worsening cough.',
'Internal Discharge Protocol DP-142 (synthetic)','internal_medicine',
ARRAY['pneumonia','antibiotics'],100,'2026-01-01','2027-01-01','Dr. A. Reviewer (demo)',
'{"complete_course": true, "warning_signs": ["fever","worsening_cough"]}'),

('medication','Hip Replacement','Hip Replacement Medication',
'Take prescribed pain medication as directed, no more than 4 doses per day. Take Aspirin 81mg once daily for blood clot prevention for 4 weeks.',
'Internal Discharge Protocol DP-150 (synthetic)','orthopedics',
ARRAY['post-op','arthroplasty','anticoagulant'],100,'2026-01-01','2027-01-01','Dr. A. Reviewer (demo)',
'{"drug": "Aspirin 81mg", "duration_weeks": 4, "max_pain_doses_per_day": 4}');

-- ============================================================================
-- Follow-up Rules — read by the Follow-up Scheduling agent
-- metadata.followup_days is the machine-actionable number; content is prose.
-- ============================================================================

INSERT INTO knowledge_base
(category, lookup_key, title, content, source, specialty, tags, priority, effective_date, review_by_date, approved_by, metadata)
VALUES

('followup','Post Appendectomy','General Surgery Follow-up',
'Schedule General Surgery follow-up within 14 days.',
'Scheduling Policy SP-9 (synthetic)','general_surgery',
ARRAY['post-op'],100,'2026-01-01','2027-01-01','Scheduling Committee (demo)',
'{"followup_days": 14, "provider_type": "general_surgery"}'),

('followup','Heart Failure Exacerbation','Cardiology Follow-up',
'Schedule Cardiology follow-up within 7 days.',
'Scheduling Policy SP-9 (synthetic)','cardiology',
ARRAY['chf','readmission-prevention'],100,'2026-01-01','2027-01-01','Scheduling Committee (demo)',
'{"followup_days": 7, "provider_type": "cardiology"}'),

('followup','Stroke','Neurology Follow-up',
'Schedule Neurology follow-up within 7 days.',
'Scheduling Policy SP-9 (synthetic)','neurology',
ARRAY['stroke'],100,'2026-01-01','2027-01-01','Scheduling Committee (demo)',
'{"followup_days": 7, "provider_type": "neurology"}'),

('followup','COPD','Pulmonology Follow-up',
'Schedule Pulmonology follow-up within 14 days.',
'Scheduling Policy SP-9 (synthetic)','pulmonology',
ARRAY['copd'],100,'2026-01-01','2027-01-01','Scheduling Committee (demo)',
'{"followup_days": 14, "provider_type": "pulmonology"}'),

('followup','Type 2 Diabetes','Endocrinology Follow-up',
'Schedule Endocrinology follow-up within 5 days.',
'Scheduling Policy SP-9 (synthetic)','endocrinology',
ARRAY['diabetes'],100,'2026-01-01','2027-01-01','Scheduling Committee (demo)',
'{"followup_days": 5, "provider_type": "endocrinology"}'),

('followup','Hip Fracture','Orthopedic Follow-up',
'Schedule Orthopedic follow-up within 14 days.',
'Scheduling Policy SP-9 (synthetic)','orthopedics',
ARRAY['fracture','post-op'],100,'2026-01-01','2027-01-01','Scheduling Committee (demo)',
'{"followup_days": 14, "provider_type": "orthopedics"}'),

-- priority 90: preferred orthopedics rule when retrieving by specialty
('followup','Hip Replacement','Orthopedic Follow-up (post-arthroplasty)',
'Schedule Orthopedic follow-up within 7 days.',
'Scheduling Policy SP-9 (synthetic)','orthopedics',
ARRAY['arthroplasty','post-op'],90,'2026-01-01','2027-01-01','Scheduling Committee (demo)',
'{"followup_days": 7, "provider_type": "orthopedics"}');

-- ============================================================================
-- Insurance Network — read by the Referral Routing agent
-- lookup_key is payer:specialty; 'payer:*' covers every specialty for that
-- payer. Agent code should try the exact key first, then the payer:* wildcard
-- (both shown in the sample queries below).
-- ============================================================================

INSERT INTO knowledge_base
(category, lookup_key, title, content, source, specialty, tags, priority, effective_date, review_by_date, approved_by, metadata)
VALUES

('insurance','Cigna:general_surgery','Cigna Network',
'General Surgery is available in-network.',
'Cigna Network Contract 2026 (synthetic)','general_surgery',
ARRAY['cigna','in-network'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false}'),

('insurance','Cigna:cardiology','Cigna Network',
'Cardiology is available in-network.',
'Cigna Network Contract 2026 (synthetic)','cardiology',
ARRAY['cigna','in-network'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false}'),

('insurance','Cigna:neurology','Cigna Network',
'Neurology is available in-network.',
'Cigna Network Contract 2026 (synthetic)','neurology',
ARRAY['cigna','in-network'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false}'),

('insurance','Aetna:cardiology','Aetna Network',
'Cardiology is available in-network.',
'Aetna Network Contract 2026 (synthetic)','cardiology',
ARRAY['aetna','in-network'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false}'),

('insurance','Aetna:pulmonology','Aetna Network',
'Pulmonology is available in-network.',
'Aetna Network Contract 2026 (synthetic)','pulmonology',
ARRAY['aetna','in-network'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false}'),

('insurance','Aetna:endocrinology','Aetna Network',
'Endocrinology is available in-network. Prior authorization is required.',
'Aetna Network Contract 2026 (synthetic)','endocrinology',
ARRAY['aetna','in-network','prior-auth'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": true}'),

('insurance','UnitedHealthcare:neurology','UnitedHealthcare Network',
'Neurology is available in-network.',
'UHC Network Contract 2026 (synthetic)','neurology',
ARRAY['uhc','in-network'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false}'),

('insurance','UnitedHealthcare:pulmonology','UnitedHealthcare Network',
'Pulmonology is available in-network.',
'UHC Network Contract 2026 (synthetic)','pulmonology',
ARRAY['uhc','in-network'],100,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false}'),

-- Wildcard row: priority 200 so any exact payer:specialty row (100) beats it.
('insurance','Medicare:*','Medicare',
'All Medicare participating providers are supported.',
'CMS Participation Rules (synthetic)',NULL,
ARRAY['medicare','wildcard'],200,'2026-01-01','2026-12-31','Network Ops (demo)',
'{"in_network": true, "prior_auth_required": false, "wildcard": true}');

-- ============================================================================
-- Discharge Policies — read by the Discharge Readiness agent
-- metadata.required_before_discharge is the checklist as data.
-- ============================================================================

INSERT INTO knowledge_base
(category, lookup_key, title, content, source, specialty, tags, priority, effective_date, review_by_date, approved_by, metadata)
VALUES

('policy','Post Appendectomy','Appendectomy Policy',
'Patient may be discharged home when pain is controlled, tolerating diet, incision is clean and dry, discharge medications reviewed, and surgical follow-up arranged.',
'Discharge Policy Manual §4.2 (synthetic)','general_surgery',
ARRAY['post-op','discharge-criteria'],100,'2026-01-01','2027-01-01','Clinical Governance Board (demo)',
'{"required_before_discharge": ["pain_controlled","tolerating_diet","incision_clean","meds_reviewed","followup_arranged"]}'),

('policy','Stroke','Stroke Policy',
'Confirm home health services, caregiver education, medication reconciliation, and neurology follow-up before discharge.',
'Discharge Policy Manual §4.7 (synthetic)','neurology',
ARRAY['stroke','discharge-criteria','home-health'],100,'2026-01-01','2027-01-01','Clinical Governance Board (demo)',
'{"required_before_discharge": ["home_health_confirmed","caregiver_educated","med_reconciliation","neurology_followup"]}'),

('policy','Heart Failure Exacerbation','Heart Failure Policy',
'Confirm cardiology follow-up within 7 days, medication education, weight monitoring, and readmission prevention.',
'Discharge Policy Manual §4.5 (synthetic)','cardiology',
ARRAY['chf','discharge-criteria','readmission-prevention'],100,'2026-01-01','2027-01-01','Clinical Governance Board (demo)',
'{"required_before_discharge": ["cardiology_followup_7d","med_education","weight_monitoring_plan"]}'),

('policy','COPD','COPD Policy',
'Confirm oxygen availability, inhaler education, smoking cessation counseling, and pulmonology follow-up.',
'Discharge Policy Manual §4.9 (synthetic)','pulmonology',
ARRAY['copd','discharge-criteria','oxygen'],100,'2026-01-01','2027-01-01','Clinical Governance Board (demo)',
'{"required_before_discharge": ["oxygen_available","inhaler_education","smoking_cessation","pulmonology_followup"]}'),

('policy','Pneumonia, resolved','Pneumonia Policy',
'Ensure symptoms are improving, antibiotics reviewed if prescribed, and PCP follow-up arranged.',
'Discharge Policy Manual §4.11 (synthetic)','internal_medicine',
ARRAY['pneumonia','discharge-criteria'],100,'2026-01-01','2027-01-01','Clinical Governance Board (demo)',
'{"required_before_discharge": ["symptoms_improving","antibiotics_reviewed","pcp_followup"]}');

-- ============================================================================
-- Risk Rules — read by the Risk Escalation agent
-- metadata.action is what the agent acts on ('NEEDS_REVIEW' | 'REJECT');
-- content is the human-readable rationale shown in the audit trail.
-- REJECT rules get priority 50 so they surface above NEEDS_REVIEW on ties.
-- ============================================================================

INSERT INTO knowledge_base
(category, lookup_key, title, content, source, specialty, tags, priority, effective_date, review_by_date, approved_by, metadata)
VALUES

('risk','high_readmission_risk','Risk Rule: High Readmission Risk',
'Elevated readmission risk requires a human care coordinator to review the transition plan before it is finalized.',
'Risk Matrix RM-3 (synthetic)',NULL,
ARRAY['risk','readmission'],100,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "NEEDS_REVIEW", "severity": "medium"}'),

('risk','oxygen_required','Risk Rule: Oxygen Required',
'Home oxygen dependence must be verified (equipment delivered and working) by a human before auto-completion.',
'Risk Matrix RM-3 (synthetic)','pulmonology',
ARRAY['risk','oxygen','equipment'],100,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "NEEDS_REVIEW", "severity": "medium"}'),

('risk','needs_home_health','Risk Rule: Home Health Needed',
'Home health arrangements must be confirmed with the receiving agency by a human reviewer.',
'Risk Matrix RM-3 (synthetic)',NULL,
ARRAY['risk','home-health'],100,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "NEEDS_REVIEW", "severity": "medium"}'),

('risk','recent_icu_stay','Risk Rule: Recent ICU Stay',
'A recent ICU stay indicates clinical complexity — route to human review.',
'Risk Matrix RM-3 (synthetic)',NULL,
ARRAY['risk','icu'],100,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "NEEDS_REVIEW", "severity": "high"}'),

('risk','fall_risk','Risk Rule: Fall Risk',
'Documented fall risk requires a human to confirm the home environment and mobility plan.',
'Risk Matrix RM-3 (synthetic)',NULL,
ARRAY['risk','falls','mobility'],100,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "NEEDS_REVIEW", "severity": "medium"}'),

('risk','limited_mobility','Risk Rule: Limited Mobility',
'Limited mobility requires a human to confirm equipment and transport arrangements.',
'Risk Matrix RM-3 (synthetic)',NULL,
ARRAY['risk','mobility','equipment'],100,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "NEEDS_REVIEW", "severity": "medium"}'),

('risk','left_against_medical_advice','Risk Rule: Left AMA',
'Patient left against medical advice — the automated workflow must not proceed; reject and route to the care team.',
'Risk Matrix RM-3 (synthetic)',NULL,
ARRAY['risk','ama'],50,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "REJECT", "severity": "critical"}'),

('risk','missing_pcp','Risk Rule: No PCP on File',
'No primary care provider on file — follow-up cannot be anchored; reject and route to the care team to establish a PCP first.',
'Risk Matrix RM-3 (synthetic)',NULL,
ARRAY['risk','pcp'],50,'2026-01-01','2027-01-01','Quality & Safety (demo)',
'{"action": "REJECT", "severity": "critical"}');

-- ============================================================================
-- Outreach Templates — read by the Patient Outreach agent
-- {{placeholders}} are substituted by agent code before sending; the channel
-- and send-delay live in metadata so the scheduler doesn't parse prose.
-- ============================================================================

INSERT INTO knowledge_base
(category, lookup_key, title, content, source, specialty, tags, priority, effective_date, review_by_date, approved_by, metadata)
VALUES

('outreach','Post Appendectomy','Appendectomy Outreach',
'Hi {{patient_name}}, we hope your recovery is going well. Please attend your General Surgery follow-up appointment and contact us if you develop fever, severe pain, redness, or drainage.',
'Patient Comms Pack PC-2 (synthetic)','general_surgery',
ARRAY['outreach','post-op'],100,'2026-01-01','2027-01-01','Patient Experience (demo)',
'{"channel": "sms", "send_after_days": 2, "placeholders": ["patient_name"], "reading_level": "grade_6"}'),

('outreach','Heart Failure Exacerbation','Heart Failure Outreach',
'Remember to weigh yourself every morning and call your provider if you gain more than 3 pounds in one day.',
'Patient Comms Pack PC-2 (synthetic)','cardiology',
ARRAY['outreach','chf','daily-weight'],100,'2026-01-01','2027-01-01','Patient Experience (demo)',
'{"channel": "sms", "send_after_days": 1, "placeholders": [], "reading_level": "grade_6"}'),

('outreach','Stroke','Stroke Outreach',
'Your home health team will contact you shortly. Please attend your neurology appointment and continue taking your medications.',
'Patient Comms Pack PC-2 (synthetic)','neurology',
ARRAY['outreach','stroke','home-health'],100,'2026-01-01','2027-01-01','Patient Experience (demo)',
'{"channel": "phone", "send_after_days": 1, "placeholders": [], "reading_level": "grade_6"}');

-- ============================================================================
-- Sample Queries — the retrieval contract in practice
-- ============================================================================

-- Standard agent retrieval (Medication agent shown; same shape for
-- followup / policy / outreach — only category changes):
--   SELECT title, content, metadata
--   FROM knowledge_base
--   WHERE category = 'medication'
--     AND lookup_key = 'Post Appendectomy'
--     AND is_active
--   ORDER BY priority ASC, version DESC
--   LIMIT 1;

-- Referral agent — exact payer:specialty first, wildcard fallback in one
-- query (the exact row's priority 100 beats the wildcard's 200):
--   SELECT title, content, metadata
--   FROM knowledge_base
--   WHERE category = 'insurance'
--     AND lookup_key IN ('Medicare:cardiology', 'Medicare:*')
--     AND is_active
--   ORDER BY priority ASC, version DESC
--   LIMIT 1;

-- Risk escalation — fetch the machine-actionable action for a flag:
--   SELECT lookup_key, metadata->>'action' AS action, content AS rationale
--   FROM knowledge_base
--   WHERE category = 'risk'
--     AND lookup_key = ANY(ARRAY['fall_risk','missing_pcp'])  -- case.risk_flags
--     AND is_active
--   ORDER BY priority ASC;

-- Governance — content overdue for clinical re-review:
--   SELECT category, lookup_key, title, review_by_date
--   FROM knowledge_base
--   WHERE is_active AND review_by_date < CURRENT_DATE
--   ORDER BY review_by_date;

-- Revising content (never UPDATE/DELETE — insert the new version, retire the old):
--   INSERT INTO knowledge_base (category, lookup_key, title, content, version, ...)
--   VALUES ('followup', 'Stroke', 'Neurology Follow-up',
--           'Schedule Neurology follow-up within 5 days.', 2, ...);
--   UPDATE knowledge_base
--     SET is_active = FALSE, updated_at = NOW()
--     WHERE category = 'followup' AND lookup_key = 'Stroke' AND version = 1;

-- ============================================================================
-- Future: semantic search. When lookups need to survive paraphrased
-- diagnoses ("CVA" vs "Stroke"), add pgvector and an embedding column:
--   CREATE EXTENSION IF NOT EXISTS vector;
--   ALTER TABLE knowledge_base ADD COLUMN embedding vector(768);
--   CREATE INDEX ON knowledge_base USING hnsw (embedding vector_cosine_ops);
-- Exact-key lookup stays as the fast path; vector search becomes the
-- fallback when no exact key matches. Not enabled yet — the bundled
-- postgres:16-alpine image does not ship the pgvector extension.
-- ============================================================================
