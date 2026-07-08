"""Step 2 — three synthetic fixture cases, reused by every step from here on.

Patient A — clean, happy path: known payer, known specialty, PCP on file.
Patient B — payer/network is unrecognized, the kind of thing that stalls a
  prior-auth in real life. Should land below the confidence threshold.
Patient C — no PCP on file plus active risk flags. Should land well below
  the threshold and hit the human review gate hardest.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from carebridge.models import CaseStatus, DataSource, DischargeDisposition, TransitionCase

CASE_A_CLEAN = TransitionCase(
    case_id="case-A",
    patient_id="patient-001",
    admitting_facility="St. Vincent Medical Center",
    discharge_date=date(2026, 7, 1),
    discharge_disposition=DischargeDisposition.HOME,
    primary_diagnosis="Type 2 diabetes, controlled",
    has_pcp_on_file=True,
    payer="Medicare",
    referral_specialty="endocrinology",
    risk_flags=[],
    source=DataSource.SYNTHETIC,
    source_message_id="synthetic-case-A",
    received_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
)

CASE_B_PAYER_DELAY = TransitionCase(
    case_id="case-B",
    patient_id="patient-002",
    admitting_facility="St. Vincent Medical Center",
    discharge_date=date(2026, 7, 2),
    discharge_disposition=DischargeDisposition.HOME_HEALTH,
    primary_diagnosis="Post-surgical recovery, hip replacement",
    has_pcp_on_file=True,
    payer="Regional Health Plan Co",  # not in the routing agent's known network
    referral_specialty="orthopedics",
    risk_flags=["prior_auth_required"],
    source=DataSource.SYNTHETIC,
    source_message_id="synthetic-case-B",
    received_at=datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
)

CASE_C_HIGH_RISK = TransitionCase(
    case_id="case-C",
    patient_id="patient-003",
    admitting_facility="St. Vincent Medical Center",
    discharge_date=date(2026, 7, 3),
    discharge_disposition=DischargeDisposition.HOME,
    primary_diagnosis="Congestive heart failure exacerbation",
    has_pcp_on_file=False,
    payer="Medicare",
    referral_specialty="cardiology",
    risk_flags=["no_pcp_on_file", "high_readmission_risk", "prior_30day_admission"],
    source=DataSource.SYNTHETIC,
    source_message_id="synthetic-case-C",
    received_at=datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc),
)

ALL_FIXTURES = [CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK]

# Templates the API's "New Discharge" action clones from — the demo-phase
# ingress the doc describes: "a New Discharge button... drops a fixture JSON
# into the normalization layer" instead of a real EHR feed.
FIXTURE_TEMPLATES: dict[str, TransitionCase] = {
    "clean": CASE_A_CLEAN,
    "payer_delay": CASE_B_PAYER_DELAY,
    "high_risk": CASE_C_HIGH_RISK,
}


def new_case_from_template(template_key: str) -> TransitionCase:
    """A fresh case cloned from a template, with its own id and timestamps —
    so the same template can be dropped in repeatedly without colliding with
    a previous run's row in the DB."""
    template = FIXTURE_TEMPLATES[template_key]
    suffix = uuid4().hex[:6]
    now = datetime.now(timezone.utc)
    return template.model_copy(
        update={
            "case_id": f"{template.case_id}-{suffix}",
            "patient_id": f"{template.patient_id}-{suffix}",
            "status": CaseStatus.RECEIVED,
            "received_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
