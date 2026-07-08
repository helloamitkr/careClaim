"""ORM mappings for the `portal` schema.

Declared on a separate Base from the clinical tables so `create_all()` on the
clinical metadata can never create — or drop — the identity linkage. The portal
schema is owned by DDL in dbmigration/002_roles_and_portal_schema.sql.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PortalBase(DeclarativeBase):
    pass


class PortalUser(PortalBase):
    __tablename__ = "portal_user"
    __table_args__ = {"schema": "portal"}

    portal_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    patient_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_hmac: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="invited")
    # Vestigial: nothing increments it. Login is a passwordless magic link, so
    # there is no credential to brute-force. Lockout is `status != 'active'`.
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EnrollmentToken(PortalBase):
    __tablename__ = "enrollment_token"
    __table_args__ = {"schema": "portal"}

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginToken(PortalBase):
    __tablename__ = "login_token"
    __table_args__ = {"schema": "portal"}

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortalSession(PortalBase):
    __tablename__ = "portal_session"
    __table_args__ = {"schema": "portal"}

    session_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    portal_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portal.portal_user.portal_user_id"), nullable=False
    )
    # Denormalised from portal_user on purpose: this column *is* the
    # authorization key, and a join is one more place to get it wrong.
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    # Populated, never read. CSRF is SameSite=Strict only — see auth.py. This is
    # where a real double-submit check would start if a cookie-authenticated
    # state-changing route ever appears.
    csrf_token: Mapped[str] = mapped_column(String, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PhiAccessLog(PortalBase):
    """Append-only. UPDATE/DELETE are revoked from every runtime DB role, so a
    bug (or an attacker with the app's credentials) cannot rewrite history."""

    __tablename__ = "phi_access_log"
    __table_args__ = {"schema": "portal"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    patient_id: Mapped[str | None] = mapped_column(String)
    case_id: Mapped[str | None] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    source_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
