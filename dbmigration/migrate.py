#!/usr/bin/env python
"""CareBridge AI — database migration runner.

Creates everything the application needs, in dependency order, and inserts no
patient data of any kind.

    001  Model tables      cases, events, agent_decisions, audit_log,
                           case_workflow — created from the SQLAlchemy models,
                           because they are already declared there and a second
                           hand-written copy would drift.
    002  Roles + portal    carebridge_owner / _app / _portal, the `portal`
                           schema and its tables. Needs superuser.
    003  RLS + view        Row-level security on `cases`, the portal read
                           model, and the grants that bound each role.
    005  Backfill          A case_workflow row for every case that predates it;
                           without one a case is invisible in both panels.
    004  Knowledge base    Reference content the RAG agents retrieve. Clinical
                           reference data, not sample cases — opt in with
                           --with-knowledge-base.

Every step is idempotent: re-running is safe and is the intended way to apply
new migrations to an existing database.

USAGE

    python dbmigration/migrate.py                       # steps 001-003
    python dbmigration/migrate.py --with-knowledge-base # also step 004
    python dbmigration/migrate.py --check               # report, change nothing

Steps 002 and 003 create roles and reassign ownership, so run this as a
superuser. The application itself should then connect as `carebridge_app` and
the portal as `carebridge_portal` — see PATIENT_PORTAL_DESIGN.md §2, finding #3.

    ADMIN_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/careai \
        python dbmigration/migrate.py

Falls back to DATABASE_URL when ADMIN_DATABASE_URL is unset.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent
REPO_ROOT = MIGRATIONS_DIR.parent

# The backend package is not necessarily installed when this script runs.
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:  # pragma: no cover — dotenv is optional for this script
    pass

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

# Ordered. `None` means "created by the models, not by a .sql file".
STEPS: list[tuple[str, str, str | None]] = [
    ("001", "model tables (cases, events, agent_decisions, audit_log, case_workflow)", None),
    ("002", "roles + portal schema", "002_roles_and_portal_schema.sql"),
    ("003", "row-level security + portal view", "003_rls_and_portal_view.sql"),
    ("005", "backfill case_workflow for pre-existing cases", "005_backfill_case_workflow.sql"),
]
KNOWLEDGE_BASE = ("004", "knowledge base reference content", "004_knowledge_base.sql")

MODEL_TABLES = ["cases", "events", "agent_decisions", "audit_log", "case_workflow"]


def admin_url() -> str:
    url = os.environ.get("ADMIN_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit(
            "No database URL. Set ADMIN_DATABASE_URL (or DATABASE_URL) — e.g.\n"
            "  ADMIN_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/careai"
        )
    return url


def create_model_tables(engine) -> None:
    """Step 001. Imports register each model on the shared metadata; without
    them create_all() silently produces an incomplete schema."""
    from carebridge.audit import AuditLogRecord  # noqa: F401
    from carebridge.persistence import (  # noqa: F401
        AgentDecisionRecord,
        Base,
        CaseRecord,
        EventRecord,
    )
    from carebridge.services.workflow import CaseWorkflow  # noqa: F401

    Base.metadata.create_all(engine)


def run_sql_file(engine, filename: str) -> None:
    path = MIGRATIONS_DIR / filename
    if not path.exists():
        sys.exit(f"Missing migration file: {path}")
    with engine.begin() as conn:
        conn.execute(text(path.read_text()))


def report(engine) -> int:
    """--check: say what exists. Touches nothing."""
    insp = inspect(engine)
    public = set(insp.get_table_names())
    missing = [t for t in MODEL_TABLES if t not in public]

    print("Model tables:")
    for table in MODEL_TABLES:
        print(f"  {'ok  ' if table in public else 'MISSING'} {table}")

    with engine.connect() as conn:
        roles = conn.execute(
            text(
                "SELECT rolname FROM pg_roles "
                "WHERE rolname IN ('carebridge_owner','carebridge_app','carebridge_portal')"
            )
        ).scalars().all()
        has_view = conn.execute(
            text(
                "SELECT 1 FROM information_schema.views "
                "WHERE table_schema='portal' AND table_name='portal_case_view'"
            )
        ).scalar() is not None
        rls = conn.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname='cases'")
        ).scalar()
        kb = "knowledge_base" in public

    print("\nRoles:      " + (", ".join(sorted(roles)) if roles else "none — run step 002"))
    print(f"Portal view:{'  present' if has_view else '  MISSING — run step 003'}")
    print(f"RLS on cases:{' enabled' if rls else ' DISABLED — run step 003'}")
    print(f"Knowledge base: {'loaded' if kb else 'not loaded (optional)'}")

    ok = not missing and len(roles) == 3 and has_view and bool(rls)
    print("\n" + ("Database is fully migrated." if ok else "Migrations are pending."))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--with-knowledge-base",
        action="store_true",
        help="also load step 004, the RAG reference content (no patient data)",
    )
    parser.add_argument("--check", action="store_true", help="report migration state and exit")
    args = parser.parse_args()

    url = admin_url()
    # Never print credentials.
    engine = create_engine(url, future=True)
    print(f"Target: {engine.url.render_as_string(hide_password=True)}\n")

    try:
        if args.check:
            return report(engine)

        steps = list(STEPS)
        if args.with_knowledge_base:
            steps.append(KNOWLEDGE_BASE)

        for number, description, filename in steps:
            print(f"[{number}] {description} … ", end="", flush=True)
            if filename is None:
                create_model_tables(engine)
            else:
                run_sql_file(engine, filename)
            print("ok")

        print("\nDone. No patient data was inserted.")
        if not args.with_knowledge_base:
            print("The RAG agents fall back to an in-memory seed until you run")
            print("  python dbmigration/migrate.py --with-knowledge-base")
        return 0

    except SQLAlchemyError as exc:
        print("failed", flush=True)  # completes the "[00N] … " line before stderr
        message = str(exc.orig) if getattr(exc, "orig", None) else str(exc)
        print(f"\n  {message.strip().splitlines()[0]}", file=sys.stderr)
        if "permission denied" in message or "must be superuser" in message:
            print(
                "\n  Steps 002/003 create roles and change ownership — connect as a\n"
                "  superuser via ADMIN_DATABASE_URL.",
                file=sys.stderr,
            )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
