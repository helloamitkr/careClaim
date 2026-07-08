"""Portal authentication: enrollment, passwordless login, sessions.

Design decisions and the reasons for them (§5 of PATIENT_PORTAL_DESIGN.md):

* No passwords. There is no password column anywhere. Passwordless magic links
  mean no credential to breach, no reset flow to abuse, no hashing choice to
  get wrong. Production should delegate to an OIDC provider instead.

* Enrollment is out-of-band. Staff mint a token bound to a patient_id at
  discharge; the patient exchanges token+email for an account. There is no code
  path in which a user names their own patient_id. This is the control that
  stops "sign up as patient-b691fe".

* Sessions are server-side rows, not JWTs, because a JWT cannot be revoked on
  logout or account lockout.

* The cookie value is never stored. Only its SHA-256 is, exactly as with a
  password hash: a database leak must not yield live sessions.

* CSRF is handled by SameSite=Strict on the session cookie, and nothing else.
  A double-submit token check used to exist here but was wired to no route, so
  it protected nothing while reading as if it did. The `portal_session.csrf_token`
  column survives (it is NOT NULL) and is filled with a random value nobody
  reads. If you add a state-changing route that authenticates by cookie, that
  column is where a real double-submit check would start.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from carebridge.portal.crypto import (
    email_hmac,
    encrypt_email,
    new_token,
    token_hash,
)

ENROLLMENT_TTL = timedelta(hours=72)
LOGIN_TOKEN_TTL = timedelta(minutes=10)
SESSION_IDLE_TIMEOUT = timedelta(minutes=15)   # shared/family devices
SESSION_ABSOLUTE_TIMEOUT = timedelta(hours=12)  # bounds a stolen cookie

SESSION_COOKIE = "carebridge_portal_session"

# Lockout: `portal_user.status` is the mechanism — set it to anything but
# 'active' and request_login_token() refuses. There is deliberately no
# failed-attempt counter: login is a passwordless magic link, so there is no
# credential to brute-force. The `failed_logins` column is retained (it is NOT
# NULL) but nothing reads or increments it.


class EnrollmentError(RuntimeError):
    pass


class AuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    portal_user_id: uuid.UUID
    patient_id: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enrollment (staff-initiated)
# ---------------------------------------------------------------------------


def issue_enrollment_token(engine, *, patient_id: str, issued_by: str) -> str:
    """Staff-only. Returns the raw token exactly once — deliver it out of band
    (discharge paperwork, or an address already in the EHR). Only its hash is
    persisted, so this value cannot be recovered from the database."""
    token = new_token()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO portal.enrollment_token "
                "(token_hash, patient_id, expires_at, issued_by, created_at) "
                "VALUES (:h, :pid, :exp, :by, :now)"
            ),
            {
                "h": token_hash(token),
                "pid": patient_id,
                "exp": _now() + ENROLLMENT_TTL,
                "by": issued_by,
                "now": _now(),
            },
        )
    return token


def redeem_enrollment_token(engine, *, token: str, email: str) -> str:
    """Exchange a staff-issued token for an account. The patient_id comes from
    the token row, never from the request — that is the whole security property.

    Returns the patient_id now bound to this account.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT patient_id, expires_at, used_at FROM portal.enrollment_token "
                "WHERE token_hash = :h FOR UPDATE"
            ),
            {"h": token_hash(token)},
        ).one_or_none()

        if row is None:
            raise EnrollmentError("invalid or expired enrollment token")
        if row.used_at is not None:
            raise EnrollmentError("invalid or expired enrollment token")  # single use
        if row.expires_at <= _now():
            raise EnrollmentError("invalid or expired enrollment token")

        # One message for every failure mode above: a distinct "already used"
        # would tell an attacker the token was real.

        conn.execute(
            text("UPDATE portal.enrollment_token SET used_at = :now WHERE token_hash = :h"),
            {"now": _now(), "h": token_hash(token)},
        )
        conn.execute(
            text(
                "INSERT INTO portal.portal_user "
                "(portal_user_id, patient_id, email_encrypted, email_hmac, status, created_at) "
                "VALUES (:uid, :pid, :enc, :hmac, 'active', :now) "
                "ON CONFLICT (patient_id) DO NOTHING"
            ),
            {
                "uid": uuid.uuid4(),
                "pid": row.patient_id,
                "enc": encrypt_email(email),
                "hmac": email_hmac(email),
                "now": _now(),
            },
        )
    return row.patient_id


# ---------------------------------------------------------------------------
# Passwordless login
# ---------------------------------------------------------------------------


def request_login_token(engine, *, email: str) -> str | None:
    """Mint a single-use, 10-minute login token for a known email.

    Returns None for an unknown or locked account. The *caller* must respond
    identically either way — "no account with that email" is a free account
    enumeration oracle, and for a patient portal the mere existence of an
    account is itself PHI (it says this person was discharged from here).
    """
    with engine.begin() as conn:
        user = conn.execute(
            text(
                "SELECT patient_id, status FROM portal.portal_user WHERE email_hmac = :h"
            ),
            {"h": email_hmac(email)},
        ).one_or_none()

        if user is None or user.status != "active":
            return None

        token = new_token()
        conn.execute(
            text(
                "INSERT INTO portal.login_token (token_hash, patient_id, expires_at, created_at) "
                "VALUES (:h, :pid, :exp, :now)"
            ),
            {
                "h": token_hash(token),
                "pid": user.patient_id,
                "exp": _now() + LOGIN_TOKEN_TTL,
                "now": _now(),
            },
        )
    return token


def redeem_login_token(engine, *, token: str) -> str:
    """Consume a login token and mint a session.

    Returns the raw session cookie value, once — only its hash lives in the
    database. CSRF is handled by SameSite=Strict on the cookie; the
    `csrf_token` column is filled with an unused random value because it is
    NOT NULL. See the module docstring.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT patient_id, expires_at, used_at FROM portal.login_token "
                "WHERE token_hash = :h FOR UPDATE"
            ),
            {"h": token_hash(token)},
        ).one_or_none()

        if row is None or row.used_at is not None or row.expires_at <= _now():
            raise AuthError("invalid or expired login link")

        conn.execute(
            text("UPDATE portal.login_token SET used_at = :now WHERE token_hash = :h"),
            {"now": _now(), "h": token_hash(token)},
        )

        user = conn.execute(
            text(
                "SELECT portal_user_id, status FROM portal.portal_user WHERE patient_id = :pid"
            ),
            {"pid": row.patient_id},
        ).one()
        if user.status != "active":
            raise AuthError("account is not active")

        cookie_value = new_token()
        conn.execute(
            text(
                "INSERT INTO portal.portal_session "
                "(session_hash, portal_user_id, patient_id, csrf_token, "
                " issued_at, last_seen_at, expires_at) "
                "VALUES (:h, :uid, :pid, :csrf, :now, :now, :exp)"
            ),
            {
                "h": token_hash(cookie_value),
                "uid": user.portal_user_id,
                "pid": row.patient_id,
                "csrf": new_token(),  # NOT NULL; unused — see module docstring
                "now": _now(),
                "exp": _now() + SESSION_ABSOLUTE_TIMEOUT,
            },
        )
        conn.execute(
            text("UPDATE portal.portal_user SET last_login_at = :now WHERE portal_user_id = :uid"),
            {"now": _now(), "uid": user.portal_user_id},
        )
    return cookie_value


# ---------------------------------------------------------------------------
# Session lookup (called on every authenticated request)
# ---------------------------------------------------------------------------


def resolve_session(engine, *, cookie_value: str) -> Session | None:
    """Validate a session cookie and slide its idle window.

    Returns None for anything not currently valid: unknown, revoked, past its
    absolute expiry, or idle for longer than SESSION_IDLE_TIMEOUT.
    """
    now = _now()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT portal_user_id, patient_id, last_seen_at, "
                "       expires_at, revoked_at "
                "FROM portal.portal_session WHERE session_hash = :h FOR UPDATE"
            ),
            {"h": token_hash(cookie_value)},
        ).one_or_none()

        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return None
        if now - row.last_seen_at > SESSION_IDLE_TIMEOUT:
            # Idle-expired. Revoke rather than leave it lying around reusable.
            conn.execute(
                text("UPDATE portal.portal_session SET revoked_at = :now WHERE session_hash = :h"),
                {"now": now, "h": token_hash(cookie_value)},
            )
            return None

        conn.execute(
            text("UPDATE portal.portal_session SET last_seen_at = :now WHERE session_hash = :h"),
            {"now": now, "h": token_hash(cookie_value)},
        )
    return Session(portal_user_id=row.portal_user_id, patient_id=row.patient_id)


def create_session_for(engine, *, patient_id: str) -> str:
    """DEV ONLY — mint a session from a bare patient_id, skipping enrollment and
    the magic link. Auto-creates the portal_user if it doesn't exist.

    This is the "just username + role" path. It means anyone who can name a
    patient_id can read that patient's record, which is precisely what the
    enrollment flow above exists to prevent. Gated on PORTAL_DEV_MODE; the route
    that calls it 404s in production. Everything downstream — the repository
    chokepoint, row-level security, the PHI access log — still applies, so what
    a signed-in user can reach is unchanged.
    """
    with engine.begin() as conn:
        user = conn.execute(
            text("SELECT portal_user_id FROM portal.portal_user WHERE patient_id = :pid"),
            {"pid": patient_id},
        ).one_or_none()

        if user is None:
            user_id = uuid.uuid4()
            synthetic = f"{patient_id}@dev.local"
            conn.execute(
                text(
                    "INSERT INTO portal.portal_user "
                    "(portal_user_id, patient_id, email_encrypted, email_hmac, status, created_at) "
                    "VALUES (:uid, :pid, :enc, :hmac, 'active', :now)"
                ),
                {
                    "uid": user_id,
                    "pid": patient_id,
                    "enc": encrypt_email(synthetic),
                    "hmac": email_hmac(synthetic),
                    "now": _now(),
                },
            )
        else:
            user_id = user.portal_user_id

        cookie_value = new_token()
        conn.execute(
            text(
                "INSERT INTO portal.portal_session "
                "(session_hash, portal_user_id, patient_id, csrf_token, "
                " issued_at, last_seen_at, expires_at) "
                "VALUES (:h, :uid, :pid, :csrf, :now, :now, :exp)"
            ),
            {
                "h": token_hash(cookie_value),
                "uid": user_id,
                "pid": patient_id,
                "csrf": new_token(),  # NOT NULL; unused — see module docstring
                "now": _now(),
                "exp": _now() + SESSION_ABSOLUTE_TIMEOUT,
            },
        )
    return cookie_value


def revoke_session(engine, *, cookie_value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE portal.portal_session SET revoked_at = :now "
                "WHERE session_hash = :h AND revoked_at IS NULL"
            ),
            {"now": _now(), "h": token_hash(cookie_value)},
        )
