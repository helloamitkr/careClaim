"""The domain model — what a transition-of-care case *is*.

    models/
      enums.py   DataSource, DischargeDisposition, CaseStatus
      case.py    TransitionCase

Pydantic, not SQLAlchemy. The persistence layer keeps its own row types
(`persistence.CaseRecord`, `services/workflow.CaseWorkflow`) so the shape the
agents reason over is never coupled to the shape a table happens to have.

This package replaced the single-file `models.py`; the public names below are the
same, so existing `from carebridge.models import ...` imports are unaffected.
"""

from carebridge.models.case import TransitionCase
from carebridge.models.enums import CaseStatus, DataSource, DischargeDisposition

__all__ = [
    "CaseStatus",
    "DataSource",
    "DischargeDisposition",
    "TransitionCase",
]
