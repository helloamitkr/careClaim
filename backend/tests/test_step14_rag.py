"""Step 14 — the RAG layer. Retrieval semantics on the in-memory KB, the
same lookups against the real Postgres table (loaded from knowledegebase.sql),
and proof the agents actually ground their work in what was retrieved."""

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from carebridge.agents.discharge_readiness import DischargeReadinessAgent
from carebridge.agents.medication_instruction import MedicationInstructionAgent
from carebridge.agents.patient_outreach import PatientOutreachAgent
from carebridge.bus import EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_C_HIGH_RISK
from carebridge.persistence import Database
from carebridge.rag import (
    InMemoryKnowledgeBase,
    PostgresKnowledgeBase,
    default_knowledge_base,
)
from tests.fakes import FakeLLM

KB_SQL = Path(__file__).resolve().parents[2] / "knowledegebase.sql"

# --- in-memory retrieval semantics -----------------------------------------


def test_exact_lookup_is_case_insensitive():
    kb = InMemoryKnowledgeBase()
    assert kb.lookup("medication", "type 2 diabetes") is not None
    assert kb.lookup("medication", "no such diagnosis") is None


def test_match_diagnosis_finds_key_inside_free_text():
    kb = InMemoryKnowledgeBase()
    entry = kb.match_diagnosis("medication", "Congestive heart failure exacerbation")
    assert entry is not None
    assert entry.lookup_key == "Heart Failure Exacerbation"


def test_match_diagnosis_prefers_the_most_specific_key():
    kb = InMemoryKnowledgeBase()
    # 'Heart Failure Exacerbation' and any shorter overlapping key — the
    # longest matching key must win as the most specific guidance.
    entry = kb.match_diagnosis("followup", "Congestive heart failure exacerbation")
    assert entry.lookup_key == "Heart Failure Exacerbation"


def test_insurance_wildcard_covers_unlisted_specialty():
    kb = InMemoryKnowledgeBase()
    entry = kb.lookup_insurance("Medicare", "endocrinology")  # no exact row
    assert entry is not None
    assert entry.lookup_key == "Medicare:*"


def test_insurance_exact_row_beats_the_wildcard():
    kb = InMemoryKnowledgeBase()
    entry = kb.lookup_insurance("Aetna", "endocrinology")
    assert entry.lookup_key == "Aetna:endocrinology"
    assert entry.metadata["prior_auth_required"] is True


def test_unknown_payer_returns_nothing():
    kb = InMemoryKnowledgeBase()
    assert kb.lookup_insurance("Regional Health Plan Co", "orthopedics") is None


def test_followup_rule_retrieved_by_specialty():
    kb = InMemoryKnowledgeBase()
    rule = kb.lookup_by_specialty("followup", "endocrinology")
    assert rule.metadata["followup_days"] == 5
    assert kb.lookup_by_specialty("followup", "podiatry") is None


def test_known_specialty():
    kb = InMemoryKnowledgeBase()
    assert kb.known_specialty("cardiology")
    assert not kb.known_specialty("podiatry")


# --- Postgres KB parity ------------------------------------------------------


@pytest.fixture()
def pg_kb():
    db = Database()
    try:
        with db.engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text(KB_SQL.read_text()))
    except OperationalError:
        pytest.skip("Postgres not reachable — run `docker compose up -d` in backend/")
    yield PostgresKnowledgeBase(db)


def test_postgres_kb_matches_in_memory_semantics(pg_kb):
    memory = InMemoryKnowledgeBase()

    for kb in (pg_kb, memory):
        assert kb.match_diagnosis("medication", "Congestive heart failure exacerbation").lookup_key == "Heart Failure Exacerbation"
        assert kb.lookup_insurance("Medicare", "cardiology").lookup_key == "Medicare:*"
        assert kb.lookup_insurance("Aetna", "endocrinology").metadata["prior_auth_required"] is True
        assert kb.lookup_by_specialty("followup", "endocrinology").metadata["followup_days"] == 5
        assert kb.known_specialty("orthopedics")
        assert not kb.known_specialty("podiatry")
        assert kb.match_diagnosis("medication", "unclassified condition") is None


def test_postgres_kb_is_available_flag(pg_kb):
    assert pg_kb.is_available()


# --- agents actually use what was retrieved ---------------------------------


def test_medication_prompt_contains_the_retrieved_regimen():
    llm = FakeLLM("instructions")
    agent = MedicationInstructionAgent(EventBus(), llm=llm)
    agent._decide(CASE_A_CLEAN)

    prompt = llm.calls[0]["prompt"]
    assert "Metformin" in prompt  # retrieved content, not something the LLM invents


def test_outreach_prompt_is_grounded_in_the_approved_template():
    llm = FakeLLM("message")
    agent = PatientOutreachAgent(EventBus(), llm=llm)
    agent._decide(CASE_C_HIGH_RISK)  # heart failure — a template exists

    prompt = llm.calls[0]["prompt"]
    assert "approved template" in prompt
    assert "weigh yourself every morning" in prompt


def test_outreach_without_a_template_still_drafts():
    llm = FakeLLM("message")
    agent = PatientOutreachAgent(EventBus(), llm=llm)
    decision = agent._decide(CASE_A_CLEAN)  # diabetes — no outreach template

    assert "approved template" not in llm.calls[0]["prompt"]
    assert decision.decision == "outreach_attempted_via_phone"


def test_readiness_prompt_includes_the_discharge_policy():
    llm = FakeLLM("READY")
    agent = DischargeReadinessAgent(EventBus(), llm=llm)
    agent._decide(CASE_C_HIGH_RISK)  # heart failure — a policy exists

    prompt = llm.calls[0]["prompt"]
    assert "Hospital discharge policy" in prompt
    assert "cardiology follow-up within 7 days" in prompt


def test_default_kb_is_shared():
    assert default_knowledge_base() is default_knowledge_base()
