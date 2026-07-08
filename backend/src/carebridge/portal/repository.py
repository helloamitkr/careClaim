"""The chokepoint.

Every read of clinical data on behalf of a patient goes through this module,
and every function here takes `session_patient_id` as its first argument — a
value that comes from the server-side session and never from the request.

Per-route `if` checks get forgotten on the tenth route. A single function that
*cannot* be called without the authorization key does not.

Three independent controls are stacked here (§6 of PATIENT_PORTAL_DESIGN.md):

  1. No route accepts a patient_id, so it cannot be spoofed.
  2. The WHERE clause below, which is not caller-supplied.
  3. Postgres RLS, via set_config('app.patient_id') on the portal connection —
     which is why these queries run on their own engine, as a role that has no
     privilege on `cases` at all.

Delete control 2 and control 3 still holds. Delete control 3 and control 2
still holds. That redundancy is the point.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DEFAULT_PORTAL_DATABASE_URL = (
    "postgresql+psycopg2://carebridge_portal:change-me-portal@localhost:5432/careai"
)

# The projection. Adding a column here is a deliberate act, reviewed against
# the minimum-necessary standard — unlike `SELECT *`, which would leak the next
# column somebody adds to `cases`.
_CASE_COLUMNS = """
    case_id,
    internal_status,
    primary_diagnosis,
    discharge_date,
    discharge_disposition,
    approved_summary,
    approved_at,
    updated_at
"""

_engine: Engine | None = None


def portal_engine() -> Engine:
    """Connection as `carebridge_portal`: SELECT on one view, INSERT on the
    audit log, nothing else. Kept separate from the app engine so a bug in a
    portal query cannot reach a table the portal has no business reading."""
    global _engine
    if _engine is None:
        url = os.environ.get("PORTAL_DATABASE_URL", DEFAULT_PORTAL_DATABASE_URL)
        _engine = create_engine(url, future=True, pool_pre_ping=True)
    return _engine


@dataclass(frozen=True)
class PortalCaseRow:
    case_id: str
    internal_status: str
    primary_diagnosis: str | None
    discharge_date: date | None
    discharge_disposition: str | None
    # NULL until an admin approves. The view, not this code, enforces that.
    approved_summary: str | None
    approved_at: datetime | None
    updated_at: datetime


def _row(r) -> PortalCaseRow:
    return PortalCaseRow(
        case_id=r.case_id,
        internal_status=r.internal_status,
        primary_diagnosis=r.primary_diagnosis,
        discharge_date=date.fromisoformat(r.discharge_date) if r.discharge_date else None,
        discharge_disposition=r.discharge_disposition,
        approved_summary=r.approved_summary,
        approved_at=r.approved_at,
        updated_at=r.updated_at,
    )


def scoped_to_patient(conn, session_patient_id: str) -> None:
    """Bind this transaction to one patient, for RLS.

    Public because the chat assistant's read path (portal/chat/context.py) needs
    the same binding against a different view. Every portal connection that
    touches clinical data calls this first; forgetting to means the view returns
    zero rows, not everybody's.

    set_config(..., is_local=true) rather than `SET LOCAL` because the latter
    cannot take a bound parameter — and string-interpolating a patient_id into
    DDL-ish SQL is how you get an injection in the middle of your authz control.
    """
    conn.execute(
        text("SELECT set_config('app.patient_id', :pid, true)"),
        {"pid": session_patient_id},
    )


def fetch_my_cases(session_patient_id: str) -> list[PortalCaseRow]:
    with portal_engine().begin() as conn:  # begin(): SET LOCAL needs a transaction
        scoped_to_patient(conn, session_patient_id)
        rows = conn.execute(
            text(
                f"SELECT {_CASE_COLUMNS} FROM portal.portal_case_view "
                "WHERE patient_id = :pid ORDER BY updated_at DESC"
            ),
            {"pid": session_patient_id},
        ).all()
    return [_row(r) for r in rows]


def fetch_my_case(session_patient_id: str, case_id: str) -> PortalCaseRow | None:
    """Returns None both when the case does not exist and when it belongs to
    somebody else. The caller must answer 404 for both — a 403 would confirm
    that the case_id is real, which is a slow enumeration oracle."""
    with portal_engine().begin() as conn:
        scoped_to_patient(conn, session_patient_id)
        row = conn.execute(
            text(
                f"SELECT {_CASE_COLUMNS} FROM portal.portal_case_view "
                "WHERE patient_id = :pid AND case_id = :cid"
            ),
            {"pid": session_patient_id, "cid": case_id},
        ).one_or_none()
    return _row(row) if row else None
