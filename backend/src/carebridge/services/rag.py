"""Step 14 — the RAG retrieval layer. Agents no longer carry hardcoded
knowledge dicts; they retrieve grounded content through the KnowledgeBase
interface and the LLM only rephrases what was retrieved — it never invents
clinical guidance.

Two implementations of one interface:

- PostgresKnowledgeBase — production. Reads the `knowledge_base` table
  seeded by dbmigration/004_knowledge_base.sql, following that file's
  retrieval contract: is_active only, ORDER BY priority ASC, version DESC.
- InMemoryKnowledgeBase — tests and DB-less runs (demos, unit tests). Ships
  with SEED_ENTRIES, a Python mirror of the SQL seed data. If you add rows
  to one, add them to the other.

Retrieval styles (why there are four lookup methods, not one):
- lookup            exact key — risk flags, anything with a controlled key
- match_diagnosis   free-text diagnosis contains the row's key ("Congestive
                    heart failure exacerbation" matches 'Heart Failure
                    Exacerbation'); longest key wins as most specific
- lookup_by_specialty  follow-up rules are retrieved by the receiving
                    specialty (the row's lookup_key is the diagnosis it was
                    authored for, but scheduling is per specialty)
- lookup_insurance  payer:specialty exact key first, payer:* wildcard second
                    (the wildcard rows carry a higher priority number, so
                    one ordered query resolves both)

No match is always a legal answer — agents fall to their low-confidence
path, never to a made-up one."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from carebridge.persistence import Database


class KnowledgeEntry(BaseModel):
    category: str
    lookup_key: str
    title: str | None = None
    content: str
    specialty: str | None = None
    source: str | None = None
    priority: int = 100
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBase(ABC):
    @abstractmethod
    def lookup(self, category: str, key: str) -> KnowledgeEntry | None: ...

    @abstractmethod
    def match_diagnosis(self, category: str, diagnosis: str) -> KnowledgeEntry | None: ...

    @abstractmethod
    def lookup_by_specialty(self, category: str, specialty: str) -> KnowledgeEntry | None: ...

    @abstractmethod
    def known_specialty(self, specialty: str) -> bool: ...

    def lookup_insurance(self, payer: str, specialty: str) -> KnowledgeEntry | None:
        exact = self.lookup("insurance", f"{payer}:{specialty}")
        wildcard = self.lookup("insurance", f"{payer}:*")
        if exact is not None and wildcard is not None:
            return exact if exact.priority <= wildcard.priority else wildcard
        return exact or wildcard


class InMemoryKnowledgeBase(KnowledgeBase):
    def __init__(self, entries: list[KnowledgeEntry] | None = None) -> None:
        self.entries = SEED_ENTRIES if entries is None else entries

    def _candidates(self, category: str) -> list[KnowledgeEntry]:
        return sorted(
            (e for e in self.entries if e.category == category),
            key=lambda e: e.priority,
        )

    def lookup(self, category: str, key: str) -> KnowledgeEntry | None:
        for entry in self._candidates(category):
            if entry.lookup_key.lower() == key.lower():
                return entry
        return None

    def match_diagnosis(self, category: str, diagnosis: str) -> KnowledgeEntry | None:
        matches = [
            e for e in self._candidates(category) if e.lookup_key.lower() in diagnosis.lower()
        ]
        if not matches:
            return None
        # Longest key = most specific guidance, then the priority order.
        return max(matches, key=lambda e: (len(e.lookup_key), -e.priority))

    def lookup_by_specialty(self, category: str, specialty: str) -> KnowledgeEntry | None:
        for entry in self._candidates(category):
            if entry.specialty and entry.specialty.lower() == specialty.lower():
                return entry
        return None

    def known_specialty(self, specialty: str) -> bool:
        return any(e.specialty and e.specialty.lower() == specialty.lower() for e in self.entries)


class PostgresKnowledgeBase(KnowledgeBase):
    """Reads the knowledge_base table loaded from dbmigration/004_knowledge_base.sql. The
    table lives outside the ORM metadata (it's owned by that SQL file), so
    queries are textual — the retrieval contract is enforced in each one."""

    _CONTRACT = "AND is_active ORDER BY priority ASC, version DESC LIMIT 1"

    def __init__(self, db: Database) -> None:
        self.db = db

    def is_available(self) -> bool:
        """True if the knowledge_base table exists — main.py uses this to
        fall back to the in-memory seed instead of crashing at startup."""
        from sqlalchemy import inspect

        return inspect(self.db.engine).has_table("knowledge_base")

    def _fetch_one(self, sql: str, params: dict[str, Any]) -> KnowledgeEntry | None:
        from sqlalchemy import text

        with self.db.Session() as session:
            row = session.execute(text(sql), params).mappings().first()
        if row is None:
            return None
        return KnowledgeEntry(
            category=row["category"],
            lookup_key=row["lookup_key"],
            title=row["title"],
            content=row["content"],
            specialty=row["specialty"],
            source=row["source"],
            priority=row["priority"],
            metadata=row["metadata"] or {},
        )

    def lookup(self, category: str, key: str) -> KnowledgeEntry | None:
        return self._fetch_one(
            "SELECT * FROM knowledge_base WHERE category = :category "
            f"AND lower(lookup_key) = lower(:key) {self._CONTRACT}",
            {"category": category, "key": key},
        )

    def match_diagnosis(self, category: str, diagnosis: str) -> KnowledgeEntry | None:
        exact = self.lookup(category, diagnosis)
        if exact is not None:
            return exact
        return self._fetch_one(
            "SELECT * FROM knowledge_base WHERE category = :category "
            "AND position(lower(lookup_key) IN lower(:diagnosis)) > 0 "
            "AND is_active "
            "ORDER BY length(lookup_key) DESC, priority ASC, version DESC LIMIT 1",
            {"category": category, "diagnosis": diagnosis},
        )

    def lookup_by_specialty(self, category: str, specialty: str) -> KnowledgeEntry | None:
        return self._fetch_one(
            "SELECT * FROM knowledge_base WHERE category = :category "
            f"AND lower(specialty) = lower(:specialty) {self._CONTRACT}",
            {"category": category, "specialty": specialty},
        )

    def known_specialty(self, specialty: str) -> bool:
        from sqlalchemy import text

        with self.db.Session() as session:
            return bool(
                session.execute(
                    text(
                        "SELECT 1 FROM knowledge_base "
                        "WHERE lower(specialty) = lower(:specialty) AND is_active LIMIT 1"
                    ),
                    {"specialty": specialty},
                ).first()
            )


def _e(category: str, key: str, content: str, *, title: str | None = None,
       specialty: str | None = None, priority: int = 100,
       metadata: dict[str, Any] | None = None) -> KnowledgeEntry:
    return KnowledgeEntry(
        category=category, lookup_key=key, title=title, content=content,
        specialty=specialty, source="seed (synthetic)", priority=priority,
        metadata=metadata or {},
    )


# Python mirror of dbmigration/004_knowledge_base.sql — keep the two in sync. (The SQL file's
# 'risk' category is not mirrored: no agent retrieves it yet — risk routing is
# composite-confidence-driven, see risk_escalation.py.)
SEED_ENTRIES: list[KnowledgeEntry] = [
    # medication — matched against primary_diagnosis
    _e("medication", "Post Appendectomy",
       "Take prescribed pain medication as directed. Keep the incision clean and dry. "
       "Avoid lifting heavy objects for two weeks. Seek medical attention if you develop "
       "fever, redness, swelling, or drainage.",
       title="Post Appendectomy Medication", specialty="general_surgery"),
    _e("medication", "Heart Failure Exacerbation",
       "Take Furosemide every morning. Weigh yourself daily. Limit sodium intake. "
       "Call your provider if you gain more than 3 pounds in one day.",
       title="Heart Failure Medication", specialty="cardiology"),
    _e("medication", "Stroke",
       "Take all prescribed medications exactly as directed. Monitor blood pressure daily. "
       "Do not stop blood thinners unless instructed.",
       title="Stroke Medication", specialty="neurology"),
    _e("medication", "Type 2 Diabetes",
       "Take Metformin with meals. Check blood sugar every morning before breakfast. "
       "Follow your diabetic diet.",
       title="Diabetes Medication", specialty="endocrinology"),
    _e("medication", "COPD",
       "Use inhalers exactly as prescribed. Continue oxygen therapy if ordered. "
       "Seek care immediately if breathing worsens.",
       title="COPD Medication", specialty="pulmonology"),
    _e("medication", "Pneumonia, resolved",
       "Complete the full antibiotic course if prescribed. Drink plenty of fluids and "
       "monitor for fever or worsening cough.",
       title="Pneumonia Medication", specialty="internal_medicine"),
    _e("medication", "Hip Replacement",
       "Take prescribed pain medication as directed, no more than 4 doses per day. "
       "Take Aspirin 81mg once daily for blood clot prevention for 4 weeks.",
       title="Hip Replacement Medication", specialty="orthopedics"),

    # followup — retrieved by specialty; metadata.followup_days is the number
    _e("followup", "Post Appendectomy", "Schedule General Surgery follow-up within 14 days.",
       title="General Surgery Follow-up", specialty="general_surgery",
       metadata={"followup_days": 14}),
    _e("followup", "Heart Failure Exacerbation", "Schedule Cardiology follow-up within 7 days.",
       title="Cardiology Follow-up", specialty="cardiology", metadata={"followup_days": 7}),
    _e("followup", "Stroke", "Schedule Neurology follow-up within 7 days.",
       title="Neurology Follow-up", specialty="neurology", metadata={"followup_days": 7}),
    _e("followup", "COPD", "Schedule Pulmonology follow-up within 14 days.",
       title="Pulmonology Follow-up", specialty="pulmonology", metadata={"followup_days": 14}),
    _e("followup", "Type 2 Diabetes", "Schedule Endocrinology follow-up within 5 days.",
       title="Endocrinology Follow-up", specialty="endocrinology",
       metadata={"followup_days": 5}),
    _e("followup", "Hip Fracture", "Schedule Orthopedic follow-up within 14 days.",
       title="Orthopedic Follow-up", specialty="orthopedics", metadata={"followup_days": 14}),
    _e("followup", "Hip Replacement", "Schedule Orthopedic follow-up within 7 days.",
       title="Orthopedic Follow-up (post-arthroplasty)", specialty="orthopedics",
       priority=90, metadata={"followup_days": 7}),

    # insurance — payer:specialty, payer:* wildcard at priority 200
    _e("insurance", "Cigna:general_surgery", "General Surgery is available in-network.",
       title="Cigna Network", specialty="general_surgery",
       metadata={"in_network": True, "prior_auth_required": False}),
    _e("insurance", "Cigna:cardiology", "Cardiology is available in-network.",
       title="Cigna Network", specialty="cardiology",
       metadata={"in_network": True, "prior_auth_required": False}),
    _e("insurance", "Cigna:neurology", "Neurology is available in-network.",
       title="Cigna Network", specialty="neurology",
       metadata={"in_network": True, "prior_auth_required": False}),
    _e("insurance", "Aetna:cardiology", "Cardiology is available in-network.",
       title="Aetna Network", specialty="cardiology",
       metadata={"in_network": True, "prior_auth_required": False}),
    _e("insurance", "Aetna:pulmonology", "Pulmonology is available in-network.",
       title="Aetna Network", specialty="pulmonology",
       metadata={"in_network": True, "prior_auth_required": False}),
    _e("insurance", "Aetna:endocrinology",
       "Endocrinology is available in-network. Prior authorization is required.",
       title="Aetna Network", specialty="endocrinology",
       metadata={"in_network": True, "prior_auth_required": True}),
    _e("insurance", "UnitedHealthcare:neurology", "Neurology is available in-network.",
       title="UnitedHealthcare Network", specialty="neurology",
       metadata={"in_network": True, "prior_auth_required": False}),
    _e("insurance", "UnitedHealthcare:pulmonology", "Pulmonology is available in-network.",
       title="UnitedHealthcare Network", specialty="pulmonology",
       metadata={"in_network": True, "prior_auth_required": False}),
    _e("insurance", "Medicare:*", "All Medicare participating providers are supported.",
       title="Medicare", priority=200,
       metadata={"in_network": True, "prior_auth_required": False, "wildcard": True}),

    # policy — discharge-readiness checklists, matched against diagnosis
    _e("policy", "Post Appendectomy",
       "Patient may be discharged home when pain is controlled, tolerating diet, incision "
       "is clean and dry, discharge medications reviewed, and surgical follow-up arranged.",
       title="Appendectomy Policy", specialty="general_surgery"),
    _e("policy", "Stroke",
       "Confirm home health services, caregiver education, medication reconciliation, and "
       "neurology follow-up before discharge.",
       title="Stroke Policy", specialty="neurology"),
    _e("policy", "Heart Failure Exacerbation",
       "Confirm cardiology follow-up within 7 days, medication education, weight "
       "monitoring, and readmission prevention.",
       title="Heart Failure Policy", specialty="cardiology"),
    _e("policy", "COPD",
       "Confirm oxygen availability, inhaler education, smoking cessation counseling, and "
       "pulmonology follow-up.",
       title="COPD Policy", specialty="pulmonology"),
    _e("policy", "Pneumonia, resolved",
       "Ensure symptoms are improving, antibiotics reviewed if prescribed, and PCP "
       "follow-up arranged.",
       title="Pneumonia Policy", specialty="internal_medicine"),

    # outreach — approved message templates, matched against diagnosis
    _e("outreach", "Post Appendectomy",
       "Hi {{patient_name}}, we hope your recovery is going well. Please attend your "
       "General Surgery follow-up appointment and contact us if you develop fever, severe "
       "pain, redness, or drainage.",
       title="Appendectomy Outreach", specialty="general_surgery",
       metadata={"channel": "sms", "send_after_days": 2}),
    _e("outreach", "Heart Failure Exacerbation",
       "Remember to weigh yourself every morning and call your provider if you gain more "
       "than 3 pounds in one day.",
       title="Heart Failure Outreach", specialty="cardiology",
       metadata={"channel": "sms", "send_after_days": 1}),
    _e("outreach", "Stroke",
       "Your home health team will contact you shortly. Please attend your neurology "
       "appointment and continue taking your medications.",
       title="Stroke Outreach", specialty="neurology",
       metadata={"channel": "phone", "send_after_days": 1}),
]

_DEFAULT_KB: InMemoryKnowledgeBase | None = None


def default_knowledge_base() -> InMemoryKnowledgeBase:
    """Shared in-memory KB for agents constructed without one (tests, demos).
    Production (api/main.py) injects PostgresKnowledgeBase instead."""
    global _DEFAULT_KB
    if _DEFAULT_KB is None:
        _DEFAULT_KB = InMemoryKnowledgeBase()
    return _DEFAULT_KB
