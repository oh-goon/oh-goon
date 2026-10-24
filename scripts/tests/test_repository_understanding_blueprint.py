from __future__ import annotations

import hashlib
from functools import partial
from pathlib import Path

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


def test_blueprint_scoring_dimension_and_bank_composition_dimension_are_separate() -> (
    None
):
    exam = load_exam()
    assert any(question.dimension != question.blueprint_dimension for question in exam)
    assert {question.blueprint_dimension for question in exam} == {
        "ownership",
        "execution",
        "lifecycle",
        "contracts",
        "change_impact",
        "testing",
        "structure",
        "security_isolation",
        "evidence_provenance",
    }
    assert {question.blueprint_form for question in exam} >= {
        "FACT",
        "RELATIONSHIP",
        "EXPLANATION",
        "COUNTERFACTUAL",
        "FALSE_PREMISE",
        "CHANGE_IMPACT",
        "DIAGNOSIS",
        "BOUNDARY",
        "SEQUENCE",
        "EVIDENCE_SELECTION",
    }


@pytest.mark.slow
def test_exam_snapshot_binds_blueprint_metadata_and_audit() -> None:
    snapshot = exam_snapshot()
    assert snapshot["blueprint_audit"]["policy_version"] == BLUEPRINT_POLICY_VERSION
    assert all("blueprint" in question["public"] for question in snapshot["questions"])


def test_exam_snapshot_composes_bounded_blueprint_and_source_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling import repository_understanding_material as material
    from oh_goon.repository_tooling import (
        _repository_understanding_material_snapshot as snapshot_owner,
    )

    source = b"def answer():\n    return 42\n"
    code = tmp_path / "backend/src/answer.py"
    code.parent.mkdir(parents=True)
    code.write_bytes(source)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    question = material.ExamQuestion(
        id="Q01",
        section="repository-ownership",
        severity="normal",
        prompt="Which owner supplies the answer?",
        type="FACT",
        dimension="ownership",
        difficulty="L1",
        response_expectations={"answer": True},
        evidence_scope=("backend/src/answer.py",),
        blueprint_dimension="ownership",
        blueprint_form="FACT",
        expected_claims=(
            material.ExpectedClaim("answer", "The answer owner supplies it", False),
        ),
        contradictions=tuple(
            material.Contradiction(
                f"wrong-{index}", f"Incorrect {name}", "normal", name
            )
            for index, name in enumerate(material.MISCONCEPTIONS)
        ),
        evidence_expectations=("backend/src/answer.py:1-2",),
    )
    audit = {
        "audit_identity": "sha256:" + "a" * 64,
        "questions": [
            {
                "id": "Q01",
                "health_status": "VALID",
                "calibration_state": "UNCALIBRATED",
            },
        ],
    }
    lifecycle = {
        "lifecycle_identity": "sha256:" + "b" * 64,
        "questions": [
            {
                "id": "Q01",
                "state": "ACTIVE",
                "qualification_bank_member": True,
                "score_bearing": True,
                "candidate_delivery": True,
            },
        ],
    }
    registry = {
        "registry_identity": "sha256:" + "c" * 64,
        "equivalence_families": [],
        "false_premise_question_ids": [],
        "contradiction_pressure": [],
        "canaries": [],
        "fresh_challenges": [],
        "rotation": {"canary_slots": 0, "fresh_shadow_slots": 0, "strategy": "fixture"},
    }
    monkeypatch.setattr(material, "ROOT", tmp_path)
    monkeypatch.setattr(snapshot_owner, "ROOT", tmp_path)
    monkeypatch.setattr(material, "load_exam", lambda: (question,))
    monkeypatch.setattr(material, "_question_audit_cached", lambda: audit)
    monkeypatch.setattr(material, "_question_lifecycle_cached", lambda: lifecycle)
    monkeypatch.setattr(material, "_benchmark_registry_cached", lambda: registry)
    monkeypatch.setattr(
        snapshot_owner,
        "_evidence_reference_identity",
        partial(material._evidence_reference_identity, root=tmp_path),
    )
    # Exercise the real composer without leaking a fixture result into its global
    # cache. Full bank admission is separate: its real audit describes this
    # intentionally undersized synthetic bank as unbalanced.
    monkeypatch.setattr(
        material, "_exam_snapshot_cached", material._exam_snapshot_cached.__wrapped__
    )
    monkeypatch.setattr(material, "assert_exam_release_ready", audit_exam)

    snapshot = material.exam_snapshot()

    assert len(snapshot["questions"]) == 1
    assert snapshot["blueprint_audit"] == audit_exam((question,))
    assert snapshot["blueprint_audit"]["status"] == "REJECTED"
    assert snapshot["questions"][0]["public"]["blueprint"] == {
        "concept_family": "C001-repository-ownership",
        "dimension": "ownership",
        "form": "FACT",
        "criticality": "STANDARD",
        "minimum_evidence_authority": "A4",
    }
    assert snapshot["source_version"] == "1.2.3"
    assert set(snapshot["repository_evidence_index"]) == {
        "VERSION",
        "backend/src/answer.py",
    }
    frozen = snapshot["questions"][0]["secret"]["evidence_reference_identities"][0]
    assert (
        frozen["source_sha256"]
        == frozen["range_sha256"]
        == "sha256:" + hashlib.sha256(source).hexdigest()
    )
