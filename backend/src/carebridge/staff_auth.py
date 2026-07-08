"""Staff authentication — Phase 0 of PATIENT_PORTAL_DESIGN.md.

A shared-secret bearer token. This is deliberately modest: it is a stopgap for a
real identity provider (finding #1 in the design doc says every staff route
needs one). But the state it replaces is *no authentication at all* on endpoints
that stream case ids and agent decisions, so a stopgap is a large improvement.

Two ways to present it, because the browser cannot do the first:

  Authorization: Bearer <token>   — scripts, curl, the staff CLI
  staff_session cookie            — the console. EventSource cannot set headers,
                                    so the log viewer has no other option. The
                                    cookie is httpOnly, so page scripts (and any
                                    XSS) cannot read the token back out.

When STAFF_API_TOKEN is unset the app refuses to serve these routes rather than
falling open — an auth check that disappears when misconfigured is worse than no
auth check, because it looks like it is working.
"""

from __future__ import annotations

import os

from fastapi import Cookie, Header, HTTPException

from carebridge.portal.crypto import constant_time_equals

STAFF_COOKIE = "carebridge_staff_session"


def dev_mode() -> bool:
    """PORTAL_DEV_MODE=true turns off the credential ceremony everywhere:
    patients sign in with a bare username, and the log endpoints stop asking for
    a staff token. Intended for local demos. Never set it in production — it
    means anyone who can name a patient_id can read that patient's record."""
    return os.environ.get("PORTAL_DEV_MODE", "").strip().lower() == "true"


def staff_token() -> str:
    return os.environ.get("STAFF_API_TOKEN", "").strip()


def verify_staff_token(candidate: str) -> bool:
    expected = staff_token()
    return bool(expected) and constant_time_equals(candidate, expected)


def require_staff(
    authorization: str | None = Header(default=None),
    staff_session: str | None = Cookie(default=None, alias=STAFF_COOKIE),
) -> str:
    """FastAPI dependency. Returns the actor id recorded in the audit log."""
    if dev_mode():
        return "dev"
    if not staff_token():
        raise HTTPException(
            status_code=503,
            detail="Staff authentication is not configured (set STAFF_API_TOKEN).",
        )

    presented: str | None = None
    if authorization and authorization.startswith("Bearer "):
        presented = authorization[7:]
    elif staff_session:
        presented = staff_session

    if not presented or not verify_staff_token(presented):
        raise HTTPException(status_code=401, detail="Staff credentials required")
    return "staff"
