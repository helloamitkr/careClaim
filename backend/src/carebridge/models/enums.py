"""The closed vocabularies a case is written in.

`str, Enum` on purpose: the values go straight into JSON payloads, Postgres text
columns, and event bodies, and a bare `Enum` would serialize as `CaseStatus.X`.
"""

from __future__ import annotations

from enum import Enum


class DataSource(str, Enum):
    EHR_HL7_V2 = "ehr_hl7_v2"
    EHR_FHIR_R4 = "ehr_fhir_r4"
    CCDA = "ccda"
    SYNTHETIC = "synthetic"


class DischargeDisposition(str, Enum):
    HOME = "home"
    HOME_HEALTH = "home_health"
    SNF = "snf"
    HOSPICE = "hospice"
    OTHER = "other"


class CaseStatus(str, Enum):
    """The *agent pipeline's* status, not the human workflow's. A case reaches
    COMPLETED when the agents are done; whether a clinician has approved it is
    tracked separately — see services/workflow.py."""

    RECEIVED = "received"
    IN_PROGRESS = "in_progress"
    NEEDS_REVIEW = "needs_review"
    AUTO_COMPLETED = "auto_completed"
    COMPLETED = "completed"
    REJECTED = "rejected"
