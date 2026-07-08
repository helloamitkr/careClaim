"""PHI access log — HIPAA §164.312(b).

Distinct from carebridge.audit, which records what the *agents* decided. This
records what *humans read*: who looked at whose record, when, from where, and
whether we let them.

Two rules that are easy to get wrong:

  1. Log reads, not just writes. A breach is somebody reading records they
     shouldn't; a write-only audit trail cannot see it.
  2. Log denials. A burst of `outcome='deny'` from one actor is the signal that
     somebody is walking case ids. Successes alone look identical to normal use.

Never log PHI *into* the log: record `case_id`, never the diagnosis. Note that
TransitionCase.summary() embeds the diagnosis — it must not be called from here.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import text

Outcome = Literal["allow", "deny"]
ActorType = Literal["patient", "staff", "system"]


def _valid_ip(value: str | None) -> str | None:
    """`source_ip` is an inet column. A non-address value (a proxy sending a
    hostname, a test client) must not blow up the insert — because this audit
    fails closed, a bad IP would otherwise take the whole request down with it.
    Losing the IP is acceptable; losing the audit row is not."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


class AuditWriteError(RuntimeError):
    """The disclosure could not be recorded, so it must not happen.

    Fail closed: an un-auditable read of PHI is worse than an unavailable
    portal. Callers turn this into a 503, never into a silent success.
    """


class PhiAccessAudit:
    def __init__(self, engine) -> None:
        self._engine = engine

    def record(
        self,
        *,
        actor_type: ActorType,
        actor_id: str,
        action: str,
        outcome: Outcome,
        patient_id: str | None = None,
        case_id: str | None = None,
        source_ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO portal.phi_access_log "
                        "(occurred_at, actor_type, actor_id, patient_id, case_id, "
                        " action, outcome, source_ip, user_agent) "
                        "VALUES (:ts, :atype, :aid, :pid, :cid, :act, :out, :ip, :ua)"
                    ),
                    {
                        "ts": datetime.now(timezone.utc),
                        "atype": actor_type,
                        "aid": actor_id,
                        "pid": patient_id,
                        "cid": case_id,
                        "act": action,
                        "out": outcome,
                        "ip": _valid_ip(source_ip),
                        # Bound: a hostile client controls this header.
                        "ua": (user_agent or "")[:512] or None,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.bind(component="phi_audit").exception(
                "FAILED to write phi_access_log: actor={actor} action={action}",
                actor=actor_id,
                action=action,
            )
            raise AuditWriteError(
                "PHI access could not be audited; refusing to disclose"
            ) from exc
