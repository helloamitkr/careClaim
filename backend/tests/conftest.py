"""Point the whole test suite at a throwaway database.

WHY THIS FILE EXISTS

Several tests call `Database.reset_schema()`, which does `DROP TABLE` on every
table. Without this file that runs against whatever `DATABASE_URL` names — which
in local development is the same `careai` database the running app uses. Every
`pytest` run silently deleted the developer's cases and left the test fixtures
(`case-A`, `case-B`, `case-C`) behind, so the clinician panels came up empty and
previously-open cases 404'd.

So: before any `carebridge` module is imported, repoint `DATABASE_URL` (and the
portal's separate connection) at `careai_test`, creating it if it doesn't exist.
`carebridge/__init__` calls `load_dotenv()`, which does not override variables
already present in the environment, so setting them here wins.

Override the target with `TEST_DATABASE_NAME=something_else`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "dbmigration"

DEFAULT_ADMIN_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/careai"
DEFAULT_PORTAL_URL = (
    "postgresql+psycopg2://carebridge_portal:change-me-portal@localhost:5432/careai"
)


def _with_database(url: str, name: str) -> str:
    """Swap the database name in a SQLAlchemy URL, leaving credentials alone."""
    return re.sub(r"/[^/?]+(\?|$)", f"/{name}\\1", url)


def _load_dotenv_without_overriding() -> None:
    """We need DATABASE_URL from .env to know the host/credentials, but we must
    read it *before* carebridge does, so we can rewrite the database name."""
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass


def _ensure_test_database(admin_url: str, test_db: str) -> bool:
    """Create the test database if absent. Returns False when Postgres is
    unreachable, so the existing per-test skips still apply."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    maintenance = create_engine(
        _with_database(admin_url, "postgres"), isolation_level="AUTOCOMMIT", future=True
    )
    try:
        with maintenance.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_db}
            ).scalar()
            if not exists:
                # Identifier, not a value — cannot be a bound parameter. test_db
                # comes from our own constant or an env var the developer set.
                conn.execute(text(f'CREATE DATABASE "{test_db}"'))
        return True
    except SQLAlchemyError:
        return False
    finally:
        maintenance.dispose()


def _migrate(test_url: str) -> None:
    """Roles + portal schema (002), then the model tables and RLS via
    init_schema(), then the workflow backfill (005). Idempotent."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    engine = create_engine(test_url, future=True)
    try:
        for sql_file in ("002_roles_and_portal_schema.sql",):
            path = MIGRATIONS / sql_file
            if path.exists():
                with engine.begin() as conn:
                    conn.execute(text(path.read_text()))
    except SQLAlchemyError:
        pass  # no superuser / no portal schema — portal tests skip themselves
    finally:
        engine.dispose()

    from carebridge.persistence import Database

    try:
        Database().init_schema()  # create_all + re-applies 003 (RLS + view)
    except Exception:
        pass  # unreachable Postgres; per-test skips handle it


# --- runs at import, before any test module pulls in carebridge --------------
_load_dotenv_without_overriding()

_ADMIN_URL = os.environ.get("DATABASE_URL", DEFAULT_ADMIN_URL)
_TEST_DB = os.environ.get("TEST_DATABASE_NAME", "careai_test")

if _ensure_test_database(_ADMIN_URL, _TEST_DB):
    os.environ["DATABASE_URL"] = _with_database(_ADMIN_URL, _TEST_DB)
    os.environ["PORTAL_DATABASE_URL"] = _with_database(
        os.environ.get("PORTAL_DATABASE_URL", DEFAULT_PORTAL_URL), _TEST_DB
    )
    _migrate(os.environ["DATABASE_URL"])
