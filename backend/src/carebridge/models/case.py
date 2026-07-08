"""Step 1 — the core object every agent, event, and DB row builds against."""

from __future__ import annotations

from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from carebridge.models.enums import CaseStatus, DataSource, DischargeDisposition


class TransitionCase(BaseModel):
    """A single patient's transition-of-care case, from discharge to close."""

    case_id: str
    patient_id: str
    admitting_facility: str

    # Clinical / request fields the agents actually reason over.
    discharge_date: date
    discharge_disposition: DischargeDisposition
    primary_diagnosis: str
    has_pcp_on_file: bool
    payer: str
    referral_specialty: str | None = None
    risk_flags: list[str] = Field(default_factory=list)

    status: CaseStatus = CaseStatus.RECEIVED

    # Provenance — every case knows which EHR channel it came from.
    source: DataSource
    source_message_id: str
    received_at: datetime

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        """One-line human-readable summary, used in audit log entries."""
        return (
            f"{self.case_id} · {self.discharge_disposition.value} · "
            f"{self.primary_diagnosis} · payer={self.payer}"
        )
