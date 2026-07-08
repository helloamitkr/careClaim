"""Key handling for the portal's identity linkage.

Two distinct operations on a patient's email, for two distinct reasons:

  encrypt_email  — reversible, so we can mail the patient. A stolen database
                   dump is then not a patient roster.
  email_hmac     — deterministic, so login can find the row without decrypting
                   every row in the table. A plain hash would allow offline
                   enumeration of a known email list; the HMAC key stops that.

Tokens (enrollment, login, session cookie) are stored as bare SHA-256: they are
128-bit random values, so there is nothing to enumerate and no key to manage.

PRODUCTION: PORTAL_ENC_KEY / PORTAL_HMAC_KEY belong in a KMS, not .env.
See §10 of PATIENT_PORTAL_DESIGN.md.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet

TOKEN_BYTES = 32  # 256 bits


class MissingKeyError(RuntimeError):
    """Raised at startup, not mid-request, when a portal key is absent."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingKeyError(
            f"{name} is not set — the patient portal handles PHI and refuses to "
            f"start without it. Generate one with:\n"
            f"  python -c 'import secrets,base64;"
            f"print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'"
        )
    return value


def _fernet() -> Fernet:
    return Fernet(_require("PORTAL_ENC_KEY").encode())


def encrypt_email(email: str) -> bytes:
    return _fernet().encrypt(email.strip().lower().encode())


def decrypt_email(blob: bytes) -> str:
    return _fernet().decrypt(bytes(blob)).decode()


def email_hmac(email: str) -> bytes:
    """Deterministic lookup key. Normalised so Foo@X.com == foo@x.com."""
    key = _require("PORTAL_HMAC_KEY").encode()
    return hmac.new(key, email.strip().lower().encode(), hashlib.sha256).digest()


def new_token() -> str:
    """A fresh secret to hand out. Returned once; only its hash is stored."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)


def keys_configured() -> bool:
    """Lets the app start without portal keys when the portal is disabled."""
    return bool(os.environ.get("PORTAL_ENC_KEY") and os.environ.get("PORTAL_HMAC_KEY"))
