# Database migrations

All SQL for CareBridge AI lives here. One runner applies it in dependency order.

```bash
# From the repo root, with the backend venv active:
ADMIN_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/careai \
    python dbmigration/migrate.py
```

Falls back to `DATABASE_URL` if `ADMIN_DATABASE_URL` is unset. Steps 002 and 003
create roles and change ownership, so **connect as a superuser**.

| Step | What | File |
|---|---|---|
| 001 | Model tables — `cases`, `events`, `agent_decisions`, `audit_log`, `case_workflow` | *(none — created from the SQLAlchemy models)* |
| 002 | Roles (`carebridge_owner` / `_app` / `_portal`) and the `portal` schema | `002_roles_and_portal_schema.sql` |
| 003 | Row-level security on `cases`, the portal read model, and role grants | `003_rls_and_portal_view.sql` |
| 005 | A `case_workflow` row for every case that predates it | `005_backfill_case_workflow.sql` |
| 006 | `portal.portal_case_reason_view` — the patient bot's RLS-bounded read model | `006_portal_reason_view.sql` |
| 004 | RAG reference content (**opt-in**, applied last) | `004_knowledge_base.sql` |

## No patient data

Steps 001–003 and 006 create structure only — they insert nothing. Step 005
inserts workflow *metadata* (`uploaded_by = 'unknown'`) for cases that already
exist; it creates no cases. A freshly migrated database has zero rows in every
table.

Step 004 is the one file that runs `INSERT`, which is why it is opt-in behind
`--with-knowledge-base`. What it inserts is clinical reference content the RAG
agents retrieve against (care protocols, payer rules, follow-up intervals) — no
patients, no cases, no sample records. Without it the agents fall back to an
equivalent in-memory seed, so the application still runs.

## Why 001 has no .sql file

Those five tables are already declared as SQLAlchemy models in
`backend/src/carebridge/persistence.py`, `audit.py`, and `services/workflow.py`. A
hand-written DDL copy would be a second source of truth, and the two would drift
the first time somebody adds a column. `migrate.py` imports the models and calls
`create_all()` instead.

The `.sql` files hold what SQLAlchemy cannot express: roles, `GRANT`/`REVOKE`,
row-level security policies, and the portal view.

## Commands

```bash
python dbmigration/migrate.py                        # steps 001, 002, 003, 005, 006
python dbmigration/migrate.py --with-knowledge-base  # also step 004
python dbmigration/migrate.py --check                # report state, change nothing
```

Every step is idempotent. Re-running is safe, and is how you apply a new
migration to an existing database. `--check` exits non-zero when migrations are
pending, so it works in CI or a container healthcheck.

## Adding a migration

Add `007_something.sql`, append it to `STEPS` in `migrate.py`, and keep it
idempotent (`IF NOT EXISTS`, `DROP … IF EXISTS` before `CREATE`). There is no
version table: the migrations are written to be safely re-runnable rather than
tracked, which suits a schema this size.

## A note on steps 003 and 006

`Database.init_schema()` re-applies both after every `create_all()`. This is not
redundant. `DROP TABLE cases` takes its RLS policies and both portal views down
with it, and `create_all()` recreates `agent_decisions` without 006's grant — so a
test calling `reset_schema()` would otherwise leave row-level security silently
switched off. The worst kind of failure, because everything keeps working. See
`PATIENT_PORTAL_DESIGN.md` §6.
