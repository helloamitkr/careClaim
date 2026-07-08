"""Request/response shapes for the API — kept separate from the internal
domain models (carebridge.models, carebridge.persistence) so the HTTP
contract can evolve without dragging the pipeline's internals along."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from carebridge.fixtures import FIXTURE_TEMPLATES
from carebridge.models import DataSource, DischargeDisposition


class FixtureTemplateOut(BaseModel):
    key: str
    label: str
    description: str
    sample: dict[str, Any]  # the exact clinical payload this template runs


# The fields a template actually feeds the pipeline — pulled live from the
# fixture itself so the UI's "view data" can never drift from reality.
_CLINICAL_FIELDS = {
    "discharge_date",
    "discharge_disposition",
    "primary_diagnosis",
    "has_pcp_on_file",
    "payer",
    "referral_specialty",
    "risk_flags",
}


def _sample(key: str) -> dict[str, Any]:
    return FIXTURE_TEMPLATES[key].model_dump(mode="json", include=_CLINICAL_FIELDS)


FIXTURE_TEMPLATE_OPTIONS = [
    FixtureTemplateOut(
        key="clean",
        label="Diabetic patient — clean discharge",
        description="Known payer, known specialist, PCP on file. Expected to auto-complete.",
        sample=_sample("clean"),
    ),
    FixtureTemplateOut(
        key="payer_delay",
        label="Hip replacement — payer network unclear",
        description="Insurer not recognized as in-network. Expected to need human review.",
        sample=_sample("payer_delay"),
    ),
    FixtureTemplateOut(
        key="high_risk",
        label="Heart failure — no PCP, high risk",
        description="No PCP on file, multiple risk flags. Expected to need human review.",
        sample=_sample("high_risk"),
    ),
]


class CreateCaseRequest(BaseModel):
    template: Literal["clean", "payer_delay", "high_risk"]


class IngestCaseRequest(BaseModel):
    """The EHR normalization layer's manual front door — paste JSON in
    roughly the shape of a TransitionCase and it gets normalized the same
    way a real HL7/FHIR feed would be. Only the clinical fields with no
    sensible default are required; everything else (case_id, patient_id,
    source metadata, timestamps) is auto-generated if omitted. Extra keys
    (e.g. a full fixture dump with status/created_at) are ignored rather
    than rejected, so the sample JSON shown in the UI is always valid input."""

    case_id: str | None = None
    patient_id: str | None = None
    admitting_facility: str = "St. Vincent Medical Center"
    discharge_date: date
    discharge_disposition: DischargeDisposition
    primary_diagnosis: str
    has_pcp_on_file: bool
    payer: str
    referral_specialty: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    source: DataSource = DataSource.SYNTHETIC
    source_message_id: str | None = None
    received_at: datetime | None = None


class CaseCreatedOut(BaseModel):
    case_id: str
    status: str


class CaseListItemOut(BaseModel):
    case_id: str
    patient_id: str
    status: str
    primary_diagnosis: str
    discharge_disposition: str
    payer: str
    updated_at: datetime


class AgentDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_name: str
    decision: str
    confidence: float
    rationale: str
    recorded_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    occurred_at: datetime
    produced_by: str | None
    duration_ms: float | None


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    agent_id: str
    input_summary: str
    confidence: float | None
    decision: str
    rationale: str
    reviewer: str | None
    recorded_at: datetime


class CaseDetailOut(BaseModel):
    case: dict[str, Any]
    agent_decisions: list[AgentDecisionOut]
    events: list[EventOut]
    audit: list[AuditEntryOut]
    pending_review: dict[str, Any] | None


class ReviewRequest(BaseModel):
    action: Literal["approved", "overridden", "rejected"]
    reviewer: str
    note: str | None = None


class ReviewResultOut(BaseModel):
    case_id: str
    event_type: str
    status: str


class AgentStatsOut(BaseModel):
    agent_name: str
    agent_id: str
    decisions: int
    avg_confidence: float | None
    avg_duration_ms: float | None


class StatsOut(BaseModel):
    """Aggregates for the /stats dashboard — all derived from Postgres plus
    the in-memory review queue; nothing here is tracked separately."""

    total_cases: int
    cases_by_status: dict[str, int]
    auto_complete_rate: float | None  # auto_completed / all closed cases
    avg_composite_confidence: float | None
    pending_review: int
    avg_review_wait_ms: float | None
    reviews: dict[str, int]  # approved / overridden / rejected counts
    agents: list[AgentStatsOut]
