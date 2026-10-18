from __future__ import annotations

from oh_goon.repository_tooling.repository_understanding_material import (
    load_exam,
)

from oh_goon.repository_tooling import repository_understanding_exam


def test_real_agent_audit_misconceptions_enter_as_non_scoring_shadow_questions() -> (
    None
):
    lifecycle = {
        row["id"]: row
        for row in repository_understanding_exam.question_lifecycle()["questions"]
    }
    audit = {
        row["id"]: row
        for row in repository_understanding_exam.question_audit()["questions"]
    }
    questions = {question.id: question for question in load_exam()}
    expected = {f"Q{index}" for index in range(213, 220)}

    assert expected <= questions.keys()
    for question_id in expected:
        row = lifecycle[question_id]
        assert row["state"] == "SHADOW"
        assert row["introduced_source_version"] == "1267.0.910"
        assert row["state_since_source_version"] == "1267.0.910"
        assert row["score_bearing"] is False
        assert row["qualification_bank_member"] is False
        assert row["delivery_mode"] == "SHADOW_NON_SCORE_BEARING"
        assert audit[question_id]["health_status"] == "VALID"
