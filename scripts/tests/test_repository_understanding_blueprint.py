from __future__ import annotations

from oh_goon.repository_tooling.repository_understanding_blueprint import (
    BLUEPRINT_POLICY_VERSION,
    audit_exam,
    question_blueprint,
)
import pytest

from oh_goon.repository_tooling.repository_understanding_material import (
    exam_snapshot,
    load_exam,
    qualification_blueprint_audit,
)



def test_blueprint_audit_returns_defensive_copy_of_cached_release_gate() -> None:
    first = qualification_blueprint_audit()
    first["status"] = "MUTATED"
    second = qualification_blueprint_audit()
    assert second["status"] == "BALANCED"

def test_every_question_has_concept_criticality_and_evidence_authority() -> None:
    exam = load_exam()
    assert exam
    for question in exam:
        metadata = question_blueprint(question)
        assert metadata["concept_family"].startswith("C")
        assert metadata["criticality"] in {"STANDARD", "CRITICAL"}
        assert metadata["minimum_evidence_authority"] in {"A2", "A4"}


def test_blueprint_audit_is_a_hard_balanced_bank_release_gate() -> None:
    audit = qualification_blueprint_audit()
    assert audit["policy_version"] == BLUEPRINT_POLICY_VERSION
    assert audit["enforcement"] == "HARD_BANK_RELEASE_GATE"
    assert audit["status"] == "BALANCED"
    assert audit["violations"] == []
    assert all(item["status"] == "IN_RANGE" for item in audit["dimensions"].values())
    assert all(item["status"] == "ENOUGH" for item in audit["question_forms"].values())
    assert audit["criticality"]["status"] == "IN_RANGE"
    assert audit["criticality"]["CRITICAL"] == 45


def test_blueprint_scoring_dimension_and_bank_composition_dimension_are_separate() -> None:
    exam = load_exam()
    assert any(question.dimension != question.blueprint_dimension for question in exam)
    assert {question.blueprint_dimension for question in exam} == {
        "ownership", "execution", "lifecycle", "contracts", "change_impact",
        "testing", "structure", "security_isolation", "evidence_provenance",
    }
    assert {question.blueprint_form for question in exam} >= {
        "FACT", "RELATIONSHIP", "EXPLANATION", "COUNTERFACTUAL", "FALSE_PREMISE",
        "CHANGE_IMPACT", "DIAGNOSIS", "BOUNDARY", "SEQUENCE", "EVIDENCE_SELECTION",
    }


@pytest.mark.slow
def test_exam_snapshot_binds_blueprint_metadata_and_audit() -> None:
    snapshot = exam_snapshot()
    assert snapshot["blueprint_audit"]["policy_version"] == BLUEPRINT_POLICY_VERSION
    assert all("blueprint" in question["public"] for question in snapshot["questions"])


