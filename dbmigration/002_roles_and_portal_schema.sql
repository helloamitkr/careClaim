-- Migration 002 — roles and the portal schema.
-- Phase 0 + Phase 1 of PATIENT_PORTAL_DESIGN.md.
--
-- Applied by:  python dbmigration/migrate.py   (needs a superuser connection)
-- Depends on:  001 (the model tables must exist for the GRANTs below to cover them)
--
-- Idempotent: safe to re-run. Inserts no data.
--
-- WHY: the app connected as `postgres`, a superuser. Postgres always skips
-- row-level security for superusers, so the policy in 02_rls.sql would have
-- been decorative. These roles are what make it real. Passwords here are
-- placeholders — rotate them, and put the real ones in a secrets manager.

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------

-- Owns the portal objects, including the view the portal reads through. Not a
-- superuser, so RLS applies to it — that is the whole point (see 02_rls.sql).
DO $$ BEGIN
    CREATE ROLE carebridge_owner NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- The pipeline + staff API. Full DML on clinical tables. No DDL, no superuser.
DO $$ BEGIN
    CREATE ROLE carebridge_app LOGIN PASSWORD 'change-me-app';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- The patient portal. Deny-by-default: reads exactly one view, writes one
-- audit table. It has no privilege on `cases` at all.
DO $$ BEGIN
    CREATE ROLE carebridge_portal LOGIN PASSWORD 'change-me-portal';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- Portal schema. See §4 of PATIENT_PORTAL_DESIGN.md.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS portal AUTHORIZATION carebridge_owner;

-- The identity linkage: which human is `patient-b691fe`. The most sensitive
-- table in the system, which is exactly why it is not a column on `cases`.
CREATE TABLE IF NOT EXISTS portal.portal_user (
    portal_user_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      text NOT NULL UNIQUE,
    -- Encrypted so a stolen dump is not a patient roster; HMAC'd so login can
    -- still find a row without decrypting every one of them.
    email_encrypted bytea NOT NULL,
    email_hmac      bytea NOT NULL UNIQUE,
    status          text NOT NULL DEFAULT 'invited'
                    CHECK (status IN ('invited', 'active', 'locked', 'revoked')),
    failed_logins   int  NOT NULL DEFAULT 0,
    last_login_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Out-of-band enrollment. There is deliberately no path where a user asserts
-- their own patient_id: staff mint a token at discharge, bound to the case.
CREATE TABLE IF NOT EXISTS portal.enrollment_token (
    token_hash bytea PRIMARY KEY,          -- sha256 of the token, never the token
    patient_id text        NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,                -- single use
    issued_by  text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Single-use, short-lived login links. Same hash-at-rest rule.
CREATE TABLE IF NOT EXISTS portal.login_token (
    token_hash bytea PRIMARY KEY,
    patient_id text        NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Server-side sessions: a stateless JWT cannot be revoked on logout or lockout.
CREATE TABLE IF NOT EXISTS portal.portal_session (
    session_hash   bytea PRIMARY KEY,      -- sha256 of the cookie value
    portal_user_id uuid NOT NULL REFERENCES portal.portal_user(portal_user_id),
    patient_id     text NOT NULL,          -- denormalised: it *is* the authz key
    csrf_token     text NOT NULL,
    issued_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),  -- drives idle timeout
    expires_at     timestamptz NOT NULL,                -- absolute timeout
    revoked_at     timestamptz
);

-- HIPAA §164.312(b): audit *reads*, not just writes. Append-only (see grants).
CREATE TABLE IF NOT EXISTS portal.phi_access_log (
    id          bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_type  text NOT NULL,   -- patient | staff | system
    actor_id    text NOT NULL,
    patient_id  text,            -- subject of the record
    case_id     text,
    action      text NOT NULL,   -- list_cases | read_case | login | enroll | ...
    outcome     text NOT NULL CHECK (outcome IN ('allow', 'deny')),
    source_ip   inet,
    user_agent  text
);

-- A burst of denials from one actor is the IDOR-attempt signal — index for it.
CREATE INDEX IF NOT EXISTS phi_access_log_deny_idx
    ON portal.phi_access_log (actor_id, occurred_at)
    WHERE outcome = 'deny';

ALTER TABLE portal.portal_user      OWNER TO carebridge_owner;
ALTER TABLE portal.enrollment_token OWNER TO carebridge_owner;
ALTER TABLE portal.login_token      OWNER TO carebridge_owner;
ALTER TABLE portal.portal_session   OWNER TO carebridge_owner;
ALTER TABLE portal.phi_access_log   OWNER TO carebridge_owner;

-- ---------------------------------------------------------------------------
-- Grants: deny by default, then hand back the minimum necessary.
-- ---------------------------------------------------------------------------

-- carebridge_app: pipeline + staff API.
GRANT USAGE ON SCHEMA public, portal TO carebridge_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO carebridge_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO carebridge_app;
GRANT SELECT, INSERT, UPDATE ON portal.portal_user, portal.enrollment_token,
      portal.login_token, portal.portal_session TO carebridge_app;
GRANT INSERT, SELECT ON portal.phi_access_log TO carebridge_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA portal TO carebridge_app;

-- Tables created later by SQLAlchemy (as the admin role) stay reachable.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO carebridge_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO carebridge_app;

-- carebridge_portal: the untrusted-facing role. Note what is absent — no
-- privilege of any kind on public.cases. It reads through the view only.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM carebridge_portal;
GRANT USAGE ON SCHEMA portal TO carebridge_portal;
GRANT INSERT ON portal.phi_access_log TO carebridge_portal;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA portal TO carebridge_portal;

-- The audit trail is append-only for *every* runtime role.
REVOKE UPDATE, DELETE ON portal.phi_access_log FROM carebridge_app, carebridge_portal;
