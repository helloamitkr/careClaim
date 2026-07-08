from carebridge.fixtures import ALL_FIXTURES, CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.models import CaseStatus, TransitionCase


def test_transition_case_constructs_with_defaults():
    assert CASE_A_CLEAN.status == CaseStatus.RECEIVED
    assert CASE_A_CLEAN.created_at is not None


def test_three_fixtures_exist_and_are_distinct():
    assert len(ALL_FIXTURES) == 3
    assert len({c.case_id for c in ALL_FIXTURES}) == 3


def test_fixture_a_is_clean():
    assert CASE_A_CLEAN.has_pcp_on_file is True
    assert CASE_A_CLEAN.risk_flags == []


def test_fixture_b_has_payer_friction():
    assert CASE_B_PAYER_DELAY.payer == "Regional Health Plan Co"
    assert "prior_auth_required" in CASE_B_PAYER_DELAY.risk_flags


def test_fixture_c_is_high_risk():
    assert CASE_C_HIGH_RISK.has_pcp_on_file is False
    assert len(CASE_C_HIGH_RISK.risk_flags) >= 2


def test_case_summary_is_human_readable():
    assert CASE_A_CLEAN.case_id in CASE_A_CLEAN.summary()
