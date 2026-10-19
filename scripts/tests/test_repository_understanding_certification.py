from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.repository_tooling import repository_understanding_exam as exam_module
from oh_goon.repository_tooling import repository_understanding_material as material
from oh_goon.repository_tooling.repository_understanding_graph import EXPECTED_EDGES

ROOT = Path(__file__).resolve().parents[2]
CERT = ROOT / "docs" / "certification"
QUESTIONS = CERT / "questions"
ANSWERS = CERT / "answers"
QUESTION_ID = re.compile(r"^## (Q\d{2,3})$", re.MULTILINE)
GOON = ROOT / "templates/goons/oh-goon/repository-exam/goon.yml"
PLAN = ROOT / "templates/plans/oh-goon/repository-understanding-certification/plan.yml"


def _question_ids() -> list[str]:
    found: list[str] = []
    for path in sorted(QUESTIONS.glob("*.md")):
        found.extend(QUESTION_ID.findall(path.read_text(encoding="utf-8")))
    return sorted(found, key=lambda qid: int(qid[1:]))


def _secret_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(ANSWERS.glob("*.yml")):
        if path.name in {
            "question-audit.yml",
            "question-lifecycle.yml",
            "benchmark-integrity.yml",
        }:
            continue
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert (
            payload["source_version"]
            == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        )
        entries.extend(payload["questions"])
    return sorted(entries, key=lambda item: int(str(item["id"])[1:]))


def test_frontend_native_tool_evidence_reference_includes_real_make_delegation() -> (
    None
):
    # A changed Goon source must not leave a stale or semantically empty certification range.
    question = next(item for item in _secret_entries() if item["id"] == "Q82")
    matching = [
        reference
        for reference in question["evidence_expectations"]
        if str(reference).startswith(
            "templates/goons/oh-goon/browser-targeted/goon.yml:"
        )
    ]
    assert len(matching) == 1
    start, end = map(int, str(matching[0]).rsplit(":", 1)[1].split("-"))
    source = (
        (ROOT / "templates/goons/oh-goon/browser-targeted/goon.yml")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert 1 <= start <= end <= len(source)
    assert "run: make browser-targeted-certification" in "\n".join(
        source[start - 1 : end]
    )


def _submission(tmp_path: Path, body: str = "Q01 answer\n") -> Path:
    path = tmp_path / "candidate.md"
    path.write_text(body, encoding="utf-8")
    return path


def _filled_sheet(submission: Path) -> dict[str, Any]:
    sheet = exam_module.grade_template(
        submission=submission, candidate="junior-a", mode="normal"
    )
    sheet["grader"] = {
        "type": "llm",
        "identity": "grader-a",
        "rubric_version": material.RUBRIC_VERSION,
        "configuration": {"temperature": 0},
    }
    sheet["graded_at_utc"] = "2026-09-04T10:00:00Z"
    snapshot = {item["id"]: item for item in sheet["exam_snapshot"]["questions"]}
    for grade in sheet["grades"]:
        question = snapshot[grade["id"]]
        authority_roles = question["secret"]["evidence_authority_roles"]
        canonical_evidence = authority_roles["required_authority"]
        assert canonical_evidence
        canonical_reference = canonical_evidence[0]["reference"]
        grade["question_status"] = "QUESTION_VALID"
        grade["reference_status"] = "REFERENCE_SUPPORTED"
        for claim in grade["claims"]:
            claim["result"] = "SUPPORTED"
            claim["confidence"] = 1.0
            claim["candidate_epistemic_state"] = "ASSERTED"
            claim["candidate_confidence"] = "HIGH"
            claim["evidence"] = {
                "references": [canonical_reference],
                "relevance": "STRONG",
                "support": "STRONG",
                "authority": "AUTHORITATIVE",
            }
            claim["rationale"] = None
        for contradiction in grade["contradictions"]:
            contradiction["observed"] = False
            contradiction["confidence"] = 1.0
            contradiction["rationale"] = None
        grade["assertions"] = []
    for grade, edge in zip(sheet["grades"], EXPECTED_EDGES, strict=False):
        grade["assertions"] = [
            {
                "subject": edge["subject"],
                "relation": edge["relation"],
                "object": edge["object"],
                "polarity": "AFFIRMED",
            }
        ]
        grade["rationale"] = None
    return sheet


def _first_critical_contradiction(sheet: dict[str, Any]) -> dict[str, Any]:
    snapshot = {item["id"]: item for item in sheet["exam_snapshot"]["questions"]}
    for grade in sheet["grades"]:
        expected = snapshot[grade["id"]]["secret"]["contradictions"]
        critical_ids = {
            item["id"] for item in expected if item["severity"] == "critical"
        }
        for contradiction in grade["contradictions"]:
            if contradiction["contradiction_id"] in critical_ids:
                assert isinstance(contradiction, dict)
                return contradiction
    raise AssertionError("test fixture must contain a critical contradiction")


def _write_sheet(
    tmp_path: Path, sheet: dict[str, Any], name: str = "grades.json"
) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(sheet), encoding="utf-8")
    return path


def _score(
    tmp_path: Path,
    sheet: dict[str, Any],
    submission: Path,
    name: str = "grades.json",
) -> dict[str, Any]:
    return exam_module.score(_write_sheet(tmp_path, sheet, name), submission=submission)


def test_certification_retains_220_authored_question_secret_pairs_with_157_score_bank() -> (
    None
):
    questions = _question_ids()
    secrets = [str(item["id"]) for item in _secret_entries()]
    expected = [f"Q{index:02d}" for index in range(1, 221)]
    assert questions == expected
    assert secrets == expected
    manifest = exam_module.verify()
    assert manifest["question_count"] == 220
    assert manifest["qualification_question_count"] == 157
    assert manifest["shadow_question_count"] == 63


def test_public_question_metadata_exposes_no_secret_answer_paths_or_line_references() -> (
    None
):
    path = QUESTIONS / "question-metadata.yml"
    body = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(body)
    assert payload["schema_version"] == 2
    assert (
        payload["source_version"]
        == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    )
    assert len(payload["questions"]) == 220
    assert "docs/certification/answers" not in body
    assert not re.search(r"[A-Za-z0-9_./-]+:\d+(?:-\d+)?", body)
    assert {item["type"] for item in payload["questions"]} == {
        "FACT",
        "RELATIONSHIP",
        "EXPLANATION",
        "COUNTERFACTUAL",
        "CHANGE_IMPACT",
        "FALSE_PREMISE",
    }
    assert {item["difficulty"] for item in payload["questions"]} == {
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
    }


def test_secret_uses_semantic_claims_contradictions_and_evidence_expectations() -> None:
    critical_count = 0
    critical_claims = 0
    for item in _secret_entries():
        assert "expected" not in item
        assert "boundary" not in item
        assert "scoring" not in item
        assert "required_components" not in item
        assert "fatal_errors" not in item
        claims = item["expected_claims"]
        claim_ids = [claim["id"] for claim in claims]
        assert len(claim_ids) == len(set(claim_ids))
        assert {"concept", "ownership_boundary", "consequence"}.issubset(claim_ids)
        assert all(str(claim["statement"]).strip() for claim in claims)
        if item["id"] in {"Q01", "Q02", "Q03", "Q04"}:
            assert "change_impact" in claim_ids
        assert isinstance(item["contradictions"], list)
        assert item["evidence_expectations"]
        if item["severity"] == "critical":
            critical_count += 1
            assert item["contradictions"]
            assert any(bool(claim["critical"]) for claim in claims)
        critical_claims += sum(bool(claim["critical"]) for claim in claims)
    qualification = {
        row["id"]
        for row in exam_module.question_lifecycle()["questions"]
        if row["qualification_bank_member"]
    }
    qualification_entries = [
        item for item in _secret_entries() if item["id"] in qualification
    ]
    assert sum(item["severity"] == "critical" for item in qualification_entries) == 45
    qualification_critical_questions = sum(
        item["severity"] == "critical" for item in qualification_entries
    )
    qualification_critical_claims = sum(
        bool(claim["critical"])
        for item in qualification_entries
        for claim in item["expected_claims"]
    )
    assert qualification_critical_claims >= qualification_critical_questions
    assert critical_count >= 45
    assert critical_claims >= 148


def test_exam_verify_reports_semantic_v7_profiles_and_information_model() -> None:
    manifest = exam_module.verify()
    assert (
        manifest["source_version"]
        == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    )
    assert manifest["qa_schema_version"] == 28
    assert manifest["question_schema_version"] == 2
    assert manifest["grader_schema_version"] == 6
    assert manifest["rubric_version"] == "repository-understanding.v17"
    assert manifest["scoring_policy_version"] == "repository-understanding-score.v9"
    assert manifest["qualification_policy_version"] == "authority-risk-profiles.v2"
    assert manifest["question_count"] == 220
    assert manifest["qualification_question_count"] == 157
    assert manifest["shadow_question_count"] == 63
    assert manifest["external_answerability"]["status"] == "READY"
    assert manifest["external_answerability"]["question_count"] == 63
    assert manifest["critical_question_count"] == 45
    assert manifest["blueprint_audit"]["status"] == "BALANCED"
    assert manifest["blueprint_audit"]["enforcement"] == "HARD_BANK_RELEASE_GATE"
    misconception_audit = manifest["misconception_coverage_audit"]
    assert (
        misconception_audit["policy_version"] == "repository-misconception-coverage.v7"
    )
    assert misconception_audit["ownership_question_count"] == 24
    assert misconception_audit["ownership_with_contradiction_count"] == 24
    assert misconception_audit["ownership_gap_count"] == 0
    assert misconception_audit["critical_question_count"] == 45
    assert misconception_audit["critical_with_contradiction_count"] == 45
    assert misconception_audit["critical_contradiction_gap_count"] == 0
    assert misconception_audit["critical_contradiction_gaps"] == []
    assert (
        misconception_audit["critical_with_critical_severity_contradiction_count"] == 45
    )
    assert misconception_audit["critical_severity_gap_count"] == 0
    assert misconception_audit["critical_severity_gaps"] == []
    assert misconception_audit["critical_with_critical_claim_count"] == 45
    assert misconception_audit["critical_claim_gap_count"] == 0
    assert misconception_audit["critical_claim_gaps"] == []
    assert misconception_audit["status"] == "READY"
    from oh_goon.repository_tooling.repository_understanding_material import load_exam

    lifecycle = exam_module.question_lifecycle()
    qualification = {
        row["id"] for row in lifecycle["questions"] if row["qualification_bank_member"]
    }
    assert misconception_audit["contradiction_question_count"] == sum(
        bool(question.contradictions)
        for question in load_exam()
        if question.id in qualification
    )
    assert (
        misconception_audit["covered_misconception_count"]
        == misconception_audit["misconception_count"]
        == 11
    )
    assert misconception_audit["taxonomy_gap_count"] == 0
    assert misconception_audit["taxonomy_gaps"] == []
    assert misconception_audit["severity_encoded_id_count"] == 0
    assert misconception_audit["severity_encoded_ids"] == []
    assert (
        misconception_audit["misconception_counts"]["semantic_invariant_violation"] >= 1
    )
    assert misconception_audit["misconception_counts"]["caller_owner_confusion"] >= 1
    assert (
        misconception_audit["misconception_counts"]["observer_authority_confusion"] >= 1
    )
    assert (
        misconception_audit["misconception_counts"]["desired_observed_conflation"] >= 1
    )
    assert misconception_audit["misconception_counts"]["test_as_runtime_authority"] >= 1
    assert (
        misconception_audit["misconception_counts"]["dependency_direction_reversal"]
        >= 1
    )
    assert misconception_audit["misconception_counts"]["identity_scope_confusion"] >= 1
    assert (
        misconception_audit["misconception_counts"]["duplicate_owner_assumption"] >= 1
    )
    assert (
        misconception_audit["misconception_counts"]["hidden_fallback_assumption"] >= 1
    )
    assert (
        sum(misconception_audit["misconception_counts"].values())
        == misconception_audit["contradiction_count"]
    )
    assert misconception_audit["broad_misconception_counts"] == {
        name: misconception_audit["misconception_counts"][name]
        for name in misconception_audit["broad_misconception_counts"]
    }
    assert misconception_audit["broad_contradiction_count"] == sum(
        misconception_audit["broad_misconception_counts"].values()
    )
    assert misconception_audit["specific_contradiction_count"] == (
        misconception_audit["contradiction_count"]
        - misconception_audit["broad_contradiction_count"]
    )
    assert manifest["claim_states"] == [
        "SUPPORTED",
        "PARTIAL",
        "MISSING",
        "UNSUPPORTED",
        "CONTRADICTED",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert manifest["qualification"]["default_profile"] == "change-author.v1"
    assert set(manifest["qualification"]["profiles"]) == {
        "read-only.v1",
        "change-author.v1",
        "release-author.v1",
    }
    assert (
        manifest["qualification"]["profiles"]["change-author.v1"]["overall_minimum"]
        == 85.0
    )
    assert (
        manifest["qualification"]["profiles"]["release-author.v1"][
            "critical_dimension_minimums"
        ]["ownership"]
        == 95.0
    )
    assert manifest["qualification"]["fabricated_evidence_allowed"] == 0
    authority_audit = manifest["evidence_authority_audit"]
    assert authority_audit["policy_version"] == "repository-evidence-roles.v2"
    assert authority_audit["question_count"] == 157
    assert authority_audit["reference_authority_gap_count"] == 0
    assert authority_audit["critical_reference_authority_gap_count"] == 0
    assert authority_audit["status"] == "READY"
    capability = manifest["capability_policy"]
    assert capability["policy_version"] == "repository-understanding-capabilities.v1"
    assert capability["baseline_without_certificate"] == "READ_ONLY"
    assert capability["profile_to_tier"]["read-only.v1"]["tier"] == "PLAN"
    assert capability["profile_to_tier"]["change-author.v1"]["tier"] == "STANDARD"
    assert capability["profile_to_tier"]["release-author.v1"]["tier"] == "CRITICAL"
    assert capability["time_expiry"] is None
    game_tape_policy = manifest["game_tape_policy"]
    assert game_tape_policy["policy_version"] == "repository-understanding-game-tape.v1"
    assert game_tape_policy["authority_contract"]["projection_only"] is True
    assert game_tape_policy["authority_contract"]["may_grant_capability"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["manifest_sha256"])


def test_high_risk_misconception_classes_are_bound_to_existing_question_semantics() -> (
    None
):
    from oh_goon.repository_tooling.repository_understanding_material import load_exam

    questions = {question.id: question for question in load_exam()}
    expected = {
        "Q14": "dependency_direction_reversal",
        "Q52": "observer_authority_confusion",
        "Q134": "desired_observed_conflation",
        "Q140": "test_as_runtime_authority",
    }
    for question_id, misconception_id in expected.items():
        assert misconception_id in {
            contradiction.misconception_id
            for contradiction in questions[question_id].contradictions
        }


def test_broad_caller_owner_reclassifications_are_bound_to_narrower_existing_semantics() -> (
    None
):
    from oh_goon.repository_tooling.repository_understanding_material import load_exam

    questions = {question.id: question for question in load_exam()}
    expected = {
        "Q02": "duplicate_owner_assumption",
        "Q14": "duplicate_owner_assumption",
        "Q36": "observer_authority_confusion",
        "Q43": "hidden_fallback_assumption",
        "Q44": "identity_scope_confusion",
        "Q46": "observer_authority_confusion",
        "Q48": "duplicate_owner_assumption",
        "Q60": "identity_scope_confusion",
        "Q71": "identity_scope_confusion",
        "Q80": "duplicate_owner_assumption",
        "Q106": "duplicate_owner_assumption",
        "Q114": "duplicate_owner_assumption",
        "Q115": "duplicate_owner_assumption",
        "Q120": "observer_authority_confusion",
        "Q125": "identity_scope_confusion",
        "Q137": "identity_scope_confusion",
        "Q138": "identity_scope_confusion",
        "Q139": "desired_observed_conflation",
        "Q141": "identity_scope_confusion",
    }
    for question_id, misconception_id in expected.items():
        assert misconception_id in {
            contradiction.misconception_id
            for contradiction in questions[question_id].contradictions
        }


def test_broad_semantic_invariant_reclassifications_are_bound_to_specific_existing_semantics() -> (
    None
):
    from oh_goon.repository_tooling.repository_understanding_material import load_exam

    questions = {question.id: question for question in load_exam()}
    expected = {
        "Q42": "hidden_fallback_assumption",
        "Q52": "observer_authority_confusion",
        "Q72": "hidden_fallback_assumption",
        "Q98": "absence_as_positive_evidence",
        "Q103": "duplicate_owner_assumption",
        "Q105": "duplicate_owner_assumption",
        "Q110": "identity_scope_confusion",
        "Q111": "identity_scope_confusion",
        "Q131": "observer_authority_confusion",
        "Q133": "lifecycle_state_confusion",
    }
    for question_id, misconception_id in expected.items():
        assert misconception_id in {
            contradiction.misconception_id
            for contradiction in questions[question_id].contradictions
        }


def test_critical_question_contradiction_coverage_is_a_release_gate() -> None:
    from oh_goon.repository_tooling.repository_understanding_material import (
        _assert_misconception_coverage_release_ready,
        load_exam,
    )

    questions = load_exam()
    target = next(
        question
        for question in questions
        if question.severity == "critical"
        and question.blueprint_dimension != "ownership"
    )
    without_contradiction = tuple(
        replace(question, contradictions=()) if question.id == target.id else question
        for question in questions
    )
    try:
        _assert_misconception_coverage_release_ready(without_contradiction)
    except ValueError as exc:
        assert target.id in str(exc)
    else:
        raise AssertionError(
            "critical contradiction gap must make the bank non-release-ready"
        )

    normal_only = tuple(
        replace(
            question,
            contradictions=tuple(
                replace(contradiction, severity="normal")
                for contradiction in question.contradictions
            ),
        )
        if question.id == target.id
        else question
        for question in questions
    )
    try:
        _assert_misconception_coverage_release_ready(normal_only)
    except ValueError as exc:
        assert target.id in str(exc)
    else:
        raise AssertionError(
            "critical-severity contradiction gap must make the bank non-release-ready"
        )


def test_critical_question_critical_claim_coverage_is_a_release_gate() -> None:
    from oh_goon.repository_tooling.repository_understanding_material import (
        _assert_misconception_coverage_release_ready,
        load_exam,
    )

    questions = load_exam()
    target = next(question for question in questions if question.severity == "critical")
    without_critical_claim = tuple(
        replace(
            question,
            expected_claims=tuple(
                replace(claim, critical=False) for claim in question.expected_claims
            ),
        )
        if question.id == target.id
        else question
        for question in questions
    )
    try:
        _assert_misconception_coverage_release_ready(without_critical_claim)
    except ValueError as exc:
        assert target.id in str(exc)
    else:
        raise AssertionError("critical-claim gap must make the bank non-release-ready")


def test_misconception_taxonomy_coverage_is_a_release_gate() -> None:
    from oh_goon.repository_tooling.repository_understanding_material import (
        _assert_misconception_coverage_release_ready,
        load_exam,
    )

    stripped = tuple(
        replace(
            question,
            contradictions=tuple(
                contradiction
                for contradiction in question.contradictions
                if contradiction.misconception_id != "observer_authority_confusion"
            ),
        )
        for question in load_exam()
    )
    try:
        _assert_misconception_coverage_release_ready(stripped)
    except ValueError as exc:
        assert "observer_authority_confusion" in str(exc)
    else:
        raise AssertionError(
            "taxonomy gap must make the Repository Understanding bank non-release-ready"
        )


def test_misconception_identity_does_not_encode_severity() -> None:
    from oh_goon.repository_tooling.repository_understanding_material import (
        _severity_encoded_misconception_ids,
    )

    assert (
        _severity_encoded_misconception_ids(
            ("semantic_invariant_violation", "caller_owner_confusion")
        )
        == []
    )
    assert _severity_encoded_misconception_ids(("critical_fake", "fake_normal")) == [
        "critical_fake",
        "fake_normal",
    ]


def test_exam_prepare_removes_secret_but_keeps_public_metadata(tmp_path: Path) -> None:
    destination = tmp_path / "exam"
    exam_module.prepare(destination)
    assert len(list((destination / "docs/certification/questions").glob("*.md"))) == 11
    assert (
        destination / "docs/certification/questions/question-metadata.yml"
    ).is_file()
    assert not (destination / "docs/certification/answers").exists()
    marker = json.loads((destination / ".oh-goon-certification-exam.json").read_text())
    assert marker["candidate_mode"] == "read-only"
    assert marker["information_classes"]["SECRET"].endswith(
        "physically excluded from this checkout"
    )
    assert marker["candidate_output"]["may_state_insufficient_evidence"] is True


def test_grade_template_is_observation_only_and_preserves_offline_regrade_inputs(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path, "same semantics, arbitrary wording\n")
    sheet = exam_module.grade_template(
        submission=submission, candidate="junior-a", mode="normal"
    )
    assert sheet["artifact_type"] == "repository-understanding-grader-observations"
    assert sheet["rubric_version"] == "repository-understanding.v17"
    assert sheet["qualification_profile"] == "change-author.v1"
    assert sheet["candidate_submission"]["files"][0]["text"].startswith(
        "same semantics"
    )
    assert sheet["exam_snapshot"]["questions"][0]["secret"]["expected_claims"]
    assert sheet["exam_snapshot"]["repository_evidence_index"]
    assert len(sheet["grades"]) == 157
    for grade in sheet["grades"]:
        assert "score" not in grade
        assert all(claim["result"] is None for claim in grade["claims"])
        assert all(claim["evidence"]["references"] == [] for claim in grade["claims"])
        assert grade["question_status"] is None
        assert grade["reference_status"] is None


def test_perfect_semantic_observations_produce_high_confidence_certificate(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    result = _score(tmp_path, _filled_sheet(submission), submission)
    assert result["qualified"] is True
    assert result["qualification"] == "HIGH_CONFIDENCE"
    assert result["composite_understanding"] == 100.0
    assert result["evidence_quality"] == 100.0
    assert result["mental_model_consistency"] == 100.0
    assert result["critical_failures"] == []
    assert result["gate_failures"] == []
    assert result["certificate_identity"].startswith("sha256:")


def test_dimension_scores_use_equal_question_weight_not_expected_claim_count(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    target = next(grade for grade in sheet["grades"] if grade["id"] == "Q01")
    assert len(target["claims"]) == 4
    for claim in target["claims"]:
        claim["result"] = "MISSING"
        claim["rationale"] = "fixture: whole question misunderstood"

    result = _score(tmp_path, sheet, submission, "dimension-normalization.json")
    ownership = result["dimension_normalization"]["semantic_dimensions"]["ownership"]

    assert ownership["included_questions"] == 20
    from oh_goon.repository_tooling.repository_understanding_material import load_exam

    lifecycle = exam_module.question_lifecycle()
    qualification = {
        row["id"] for row in lifecycle["questions"] if row["qualification_bank_member"]
    }
    expected_ownership_claims = sum(
        len(question.expected_claims)
        for question in load_exam()
        if question.id in qualification and question.dimension == "ownership"
    )
    assert ownership["included_claims"] == expected_ownership_claims
    assert ownership["normalized_score"] == 95.0
    assert ownership["claim_weighted_score"] == 93.8
    assert ownership["normalization_delta"] == 1.2
    assert result["dimension_scores"]["ownership"] == 95.0


def test_dimension_normalization_bank_audit_exposes_composition_without_implicit_weight() -> (
    None
):
    audit = exam_module.verify()["dimension_normalization"]

    assert (
        audit["policy_version"] == "repository-understanding-dimension-normalization.v1"
    )
    assert audit["status"] == "NORMALIZED"
    assert audit["qualification_pool"]["question_count"] == 157
    assert audit["shadow_pool"]["question_count"] == 63
    assert audit["qualification_pool"]["semantic_dimensions"]["execution"] == {
        "questions": 96,
        "expected_claims": 288,
    }
    assert audit["qualification_pool"]["semantic_dimensions"]["contracts"] == {
        "questions": 5,
        "expected_claims": 15,
    }
    assert "not implicit scoring weights" in audit["rule"]


def test_semantically_equivalent_wording_is_not_part_of_deterministic_scoring(
    tmp_path: Path,
) -> None:
    terse = _submission(
        tmp_path, "Lifecycle authority is singular; callers request cancellation.\n"
    )
    terse_sheet = _filled_sheet(terse)
    terse_result = _score(tmp_path, terse_sheet, terse, "terse-grades.json")

    verbose = tmp_path / "candidate-verbose.md"
    verbose.write_text(
        "The request path may begin at a caller, but terminal lifecycle mutation remains with one authoritative controller.\n",
        encoding="utf-8",
    )
    verbose_sheet = _filled_sheet(verbose)
    verbose_result = _score(tmp_path, verbose_sheet, verbose, "verbose-grades.json")

    assert terse_result["dimension_scores"] == verbose_result["dimension_scores"]
    assert (
        terse_result["composite_understanding"]
        == verbose_result["composite_understanding"]
    )
    assert terse_result["qualification"] == verbose_result["qualification"]
    assert (
        terse_result["certificate_identity"] != verbose_result["certificate_identity"]
    )


def test_claim_states_distinguish_partial_missing_uncertainty_unsupported_and_contradicted(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    scores: dict[str, float] = {}
    for state in [
        "PARTIAL",
        "MISSING",
        "INSUFFICIENT_EVIDENCE",
        "UNSUPPORTED",
        "CONTRADICTED",
    ]:
        sheet = _filled_sheet(submission)
        grade = next(item for item in sheet["grades"] if item["id"] == "Q01")
        claim = grade["claims"][0]
        claim["result"] = state
        claim["rationale"] = f"fixture: {state}"
        result = _score(tmp_path, sheet, submission, f"{state}.json")
        scores[state] = result["questions"][0]["score"]
        if state == "INSUFFICIENT_EVIDENCE":
            assert result["unsupported_assertions"] == 0
            assert result["contradicted_claims"] == 0
    assert scores["PARTIAL"] > scores["MISSING"] == scores["INSUFFICIENT_EVIDENCE"]
    assert scores["MISSING"] > scores["UNSUPPORTED"] > scores["CONTRADICTED"]


def test_critical_claim_misunderstanding_vetoes_qualification_even_with_high_average(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    snapshot = {item["id"]: item for item in sheet["exam_snapshot"]["questions"]}
    target = next(
        question
        for question in sheet["exam_snapshot"]["questions"]
        if question["severity"] == "critical"
        and question["public"]["dimension"] == "ownership"
        and any(claim["critical"] for claim in question["secret"]["expected_claims"])
    )
    grade = next(item for item in sheet["grades"] if item["id"] == target["id"])
    critical_claim_id = next(
        claim["id"]
        for claim in snapshot[target["id"]]["secret"]["expected_claims"]
        if claim["critical"]
    )
    claim = next(
        item for item in grade["claims"] if item["claim_id"] == critical_claim_id
    )
    claim["result"] = "CONTRADICTED"
    claim["rationale"] = "Names the forbidden authority as owner."
    result = _score(tmp_path, sheet, submission)
    assert result["composite_understanding"] > 90
    assert result["qualified"] is False
    assert any(
        item.get("kind") == "critical_claim" for item in result["critical_failures"]
    )


@pytest.mark.parametrize("question_id", [f"Q{index}" for index in range(203, 213)])
def test_hashmarks_boundary_critical_claims_are_non_compensatory(
    question_id: str, tmp_path: Path
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    snapshot = {item["id"]: item for item in sheet["exam_snapshot"]["questions"]}
    target = snapshot[question_id]
    grade = next(item for item in sheet["grades"] if item["id"] == question_id)
    critical_claim_id = next(
        claim["id"]
        for claim in target["secret"]["expected_claims"]
        if claim["critical"]
    )
    claim = next(
        item for item in grade["claims"] if item["claim_id"] == critical_claim_id
    )
    claim["result"] = "CONTRADICTED"
    claim["rationale"] = (
        "fixture: candidate crosses the frozen Hashmarks/agent/Oh-Goon boundary"
    )

    result = _score(tmp_path, sheet, submission, f"{question_id}-boundary-veto.json")

    assert result["composite_understanding"] > 90
    assert result["qualified"] is False
    assert any(
        item.get("kind") == "critical_claim"
        and item.get("question_id") == question_id
        and item.get("claim_id") == critical_claim_id
        for item in result["critical_failures"]
    )


def test_evidence_quality_is_claim_specific_and_critical_claim_evidence_can_veto(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    snapshot = {
        question["id"]: question for question in sheet["exam_snapshot"]["questions"]
    }
    target = next(
        question
        for question in sheet["exam_snapshot"]["questions"]
        if question["severity"] == "critical"
        and question["public"]["dimension"] == "ownership"
        and any(
            claim["id"] == "ownership_boundary" and claim["critical"]
            for claim in question["secret"]["expected_claims"]
        )
    )
    grade = next(item for item in sheet["grades"] if item["id"] == target["id"])
    ownership_claim = next(
        item for item in grade["claims"] if item["claim_id"] == "ownership_boundary"
    )
    ownership_claim["evidence"]["references"] = []
    result = _score(tmp_path, sheet, submission)
    scored_question = next(
        item for item in result["questions"] if item["id"] == target["id"]
    )
    scored_claim = next(
        item
        for item in scored_question["claims"]
        if item["claim_id"] == "ownership_boundary"
    )
    assert scored_claim["result"] == "SUPPORTED"
    assert scored_claim["evidence_quality"] == 0.0
    assert result["qualified"] is False
    assert any(
        item.get("kind") == "critical_evidence"
        and item.get("claim_id") == "ownership_boundary"
        for item in result["critical_failures"]
    )
    assert snapshot[target["id"]]["secret"]["evidence_expectations"]


def test_relevant_but_lower_authority_evidence_cannot_masquerade_as_governing_authority(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    target = next(
        question
        for question in sheet["exam_snapshot"]["questions"]
        if question["severity"] == "critical"
        and question["secret"]["evidence_authority_roles"][
            "canonical_minimum_authority"
        ]
        == "A1"
        and any(claim["critical"] for claim in question["secret"]["expected_claims"])
    )
    grade = next(item for item in sheet["grades"] if item["id"] == target["id"])
    critical_claim_id = next(
        claim["id"]
        for claim in target["secret"]["expected_claims"]
        if claim["critical"]
    )
    claim = next(
        item for item in grade["claims"] if item["claim_id"] == critical_claim_id
    )
    weak_reference = "scripts/tests/test_repository_understanding_certification.py:1-5"
    claim["evidence"] = {
        "references": [weak_reference],
        "relevance": "STRONG",
        "support": "STRONG",
        "authority": "AUTHORITATIVE",
    }

    result = _score(tmp_path, sheet, submission)
    scored_question = next(
        item for item in result["questions"] if item["id"] == target["id"]
    )
    scored_claim = next(
        item
        for item in scored_question["claims"]
        if item["claim_id"] == critical_claim_id
    )
    validation = result["regrade_bundle"]["evidence_validation"][target["id"]][
        "claims"
    ][critical_claim_id]

    assert validation["existence"] == "VALID"
    assert validation["authority_assessment"]["minimum_authority"] == "A1"
    assert validation["authority_assessment"]["strongest_authority"] == "A2"
    assert validation["authority_assessment"]["meets_minimum_authority"] is False
    assert scored_claim["result"] == "SUPPORTED"
    assert scored_claim["evidence_quality"] == 50.0
    assert scored_claim["evidence_authority_floor_met"] is False
    assert result["qualified"] is False
    assert any(
        item.get("kind") == "critical_evidence_authority"
        and item.get("question_id") == target["id"]
        and item.get("claim_id") == critical_claim_id
        for item in result["critical_failures"]
    )


def test_fabricated_repository_evidence_is_mechanically_detected_and_blocks_junior_qualification(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    sheet["grades"][0]["claims"][0]["evidence"]["references"] = [
        "does/not/exist.py:999"
    ]
    result = _score(tmp_path, sheet, submission)
    assert result["fabricated_evidence"] == ["Q01/concept"]
    assert result["qualified"] is False
    evidence = result["regrade_bundle"]["evidence_validation"]["Q01"]["claims"][
        "concept"
    ]
    assert evidence["existence"] == "FABRICATED"
    assert evidence["invalid_references"] == ["does/not/exist.py:999"]


def test_ambiguous_question_is_excluded_without_candidate_penalty(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    grade = sheet["grades"][0]
    grade["question_status"] = "QUESTION_AMBIGUOUS"
    grade["rationale"] = "Two repository owners are both supported by current evidence."
    result = _score(tmp_path, sheet, submission)
    first = result["questions"][0]
    assert first["included"] is False
    assert first["score"] is None
    assert result["excluded_questions"][0]["question_status"] == "QUESTION_AMBIGUOUS"
    assert result["qualified"] is True


def test_reference_answer_can_be_challenged_without_marking_candidate_wrong(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    grade = sheet["grades"][0]
    grade["reference_status"] = "REFERENCE_SUSPECT"
    grade["rationale"] = (
        "Repository evidence conflicts with the SECRET expectation; author review required."
    )
    result = _score(tmp_path, sheet, submission)
    assert result["questions"][0]["included"] is False
    assert result["excluded_questions"][0]["reference_status"] == "REFERENCE_SUSPECT"


def test_cross_answer_conflicting_owners_are_detected_as_critical_mental_model_failure(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    sheet["grades"][0]["assertions"].append(
        {
            "subject": "LifecycleController",
            "relation": "owns",
            "object": "TerminalState",
            "polarity": "AFFIRMED",
        }
    )
    sheet["grades"][1]["assertions"].append(
        {
            "subject": "Frontend",
            "relation": "owns",
            "object": "TerminalState",
            "polarity": "AFFIRMED",
        }
    )
    result = _score(tmp_path, sheet, submission)
    assert any(
        item["kind"] == "conflicting_owners" for item in result["cross_answer_findings"]
    )
    assert result["mental_model_consistency"] < 100.0
    assert result["qualified"] is False
    assert "critical_failure" in result["gate_failures"]


def test_result_exposes_exam_coverage_misconceptions_and_human_report(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    target_grade = next(grade for grade in sheet["grades"] if grade["contradictions"])
    target_grade["contradictions"][0]["observed"] = True
    result = _score(tmp_path, sheet, submission)

    assert result["exam_coverage"]["method"] == "authored-question-blueprint.v1"
    assert result["exam_coverage"]["percent"] == 100.0
    assert result["exam_coverage"]["repository_model_relationship_coverage"] == 100.0
    assert (
        result["relationship_graph"]["policy_version"]
        == "repository-relationship-graph.v2"
    )
    assert result["misconception_findings"][0]["misconception_id"]
    report = exam_module.human_report(result)
    assert "OH-GOON REPOSITORY UNDERSTANDING" in report
    assert "Exam" in report and "coverage" in report
    assert "empirical calibration UNCALIBRATED" in report
    assert "Misconceptions" in report
    assert result["qualification"] in report


def test_offline_regrading_needs_no_candidate_rerun_and_preserves_certificate_identity(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    first = _score(tmp_path, _filled_sheet(submission), submission)
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(first), encoding="utf-8")
    second = exam_module.score(result_path)
    assert second["regraded"] is True
    assert second["qualification"] == first["qualification"]
    assert second["dimension_scores"] == first["dimension_scores"]
    assert second["certificate_identity"] == first["certificate_identity"]


def test_score_rejects_changed_submission_when_exact_bytes_are_supplied(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path, "original\n")
    sheet = _filled_sheet(submission)
    grades = _write_sheet(tmp_path, sheet)
    submission.write_text("changed after grading\n", encoding="utf-8")
    try:
        exam_module.score(grades, submission=submission)
    except ValueError as error:
        assert "not bound to the supplied candidate submission" in str(error)
    else:
        raise AssertionError("changed candidate submission must fail identity binding")


def test_candidate_submission_cannot_reference_secret_answer_tree(
    tmp_path: Path,
) -> None:
    submission = _submission(
        tmp_path, "I used docs/certification/answers/01-repository-ownership.yml\n"
    )
    try:
        exam_module.grade_template(submission=submission)
    except ValueError as error:
        assert "grader-only answer material" in str(error)
    else:
        raise AssertionError("candidate SECRET-tree leakage must fail closed")


def test_score_requires_grader_identity_and_confidence(tmp_path: Path) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    sheet["grader"]["identity"] = None
    try:
        _score(tmp_path, sheet, submission)
    except ValueError as error:
        assert "grader identity" in str(error)
    else:
        raise AssertionError("grader identity must be required")

    sheet = _filled_sheet(submission)
    sheet["grades"][0]["claims"][0]["confidence"] = 1.5
    try:
        _score(tmp_path, sheet, submission, "bad-confidence.json")
    except ValueError as error:
        assert "confidence must be a number from 0 to 1" in str(error)
    else:
        raise AssertionError("invalid grader confidence must fail")


def test_harmonic_understanding_aggregation_exposes_one_weak_dimension() -> None:
    from oh_goon.repository_tooling.repository_understanding_policy import (
        weighted_harmonic,
    )

    scores = {"structure": 98.0, "execution": 96.0, "testing": 95.0, "ownership": 42.0}
    harmonic = weighted_harmonic(scores)
    arithmetic = sum(scores.values()) / len(scores)
    assert harmonic < arithmetic
    assert harmonic < 75.0


def test_source_policy_rejects_non_transient_symlink(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.repository_understanding_material import (
        _validate_source_symlinks,
    )

    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    (source / "escape").symlink_to(target)
    try:
        _validate_source_symlinks(source)
    except ValueError as error:
        assert "must not contain symlinks" in str(error)
    else:
        raise AssertionError("source symlink must fail closed")


def test_shipped_repository_understanding_plan_still_uses_one_exam_material_goon() -> (
    None
):
    goon = yaml.safe_load(GOON.read_text(encoding="utf-8"))
    plan = yaml.safe_load(PLAN.read_text(encoding="utf-8"))
    step = goon["steps"][0]
    assert step["run"].startswith(
        "uv run --offline --frozen --no-sync --project backend "
        "oh-goon-repository-understanding-exam verify"
    )
    assert step["id"] == "oh-goon-repository-understanding-exam"
    assert "capabilities" not in step
    assert step["artifacts"] == [
        {
            "path": "repository-understanding-certification-manifest.json",
            "retention": "evidence",
        }
    ]
    assert "inputs" not in plan
    assert plan["execution"] == {"purpose": "verification"}
    assert plan["goons"] == ["oh-goon/repository-exam"]


def test_qualification_profiles_are_authority_based_and_candidate_labels_do_not_change_default(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    a = exam_module.grade_template(
        submission=submission, candidate="model-a", mode="release"
    )
    b = exam_module.grade_template(
        submission=submission, candidate="model-b", mode="observer"
    )
    assert a["qualification_profile"] == "change-author.v1"
    assert b["qualification_profile"] == "change-author.v1"
    assert a["qualification_profile"] == b["qualification_profile"]


def test_release_author_profile_is_stricter_than_change_author_for_same_observations(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    change_sheet = _filled_sheet(submission)
    target = next(
        grade
        for grade in change_sheet["grades"]
        if any(claim["claim_id"] == "concept" for claim in grade["claims"])
        and next(
            q
            for q in change_sheet["exam_snapshot"]["questions"]
            if q["id"] == grade["id"]
        )["severity"]
        != "critical"
    )
    target["claims"][0]["result"] = "PARTIAL"
    target["claims"][0]["rationale"] = (
        "Only part of the expected concept is established."
    )
    change_result = _score(tmp_path, change_sheet, submission, "change-profile.json")

    release_sheet = json.loads(json.dumps(change_sheet))
    release_sheet["qualification_profile"] = "release-author.v1"
    release_result = _score(tmp_path, release_sheet, submission, "release-profile.json")

    assert change_result["qualification_profile"] == "change-author.v1"
    assert release_result["qualification_profile"] == "release-author.v1"
    assert (
        release_result["composite_understanding"]
        == change_result["composite_understanding"]
    )
    assert (release_result["qualified"] is False) or (
        change_result["qualified"] is True
    )
    assert release_result["authority_class"] == "release-author"


def test_low_confidence_fatal_judgment_requires_adjudication_instead_of_auto_failure(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    contradiction = _first_critical_contradiction(sheet)
    contradiction["observed"] = True
    contradiction["confidence"] = 0.4
    contradiction["rationale"] = "Potential misconception, but evidence is ambiguous."
    result = _score(tmp_path, sheet, submission, "adjudication.json")
    assert result["qualified"] is False
    assert result["qualification"] == "NEEDS_ADJUDICATION"
    assert result["adjudication_required"]
    assert not any(
        item.get("kind") == "critical_contradiction"
        for item in result["critical_failures"]
    )
    assert (
        result["confidence_calibration"]["grader_judgment"]["adjudication_count"] >= 1
    )


def test_high_confidence_fatal_judgment_still_vetoes_and_fabrication_is_never_adjudicated(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    contradiction = _first_critical_contradiction(sheet)
    contradiction["observed"] = True
    contradiction["confidence"] = 1.0
    result = _score(tmp_path, sheet, submission, "fatal.json")
    assert any(
        item.get("kind") == "critical_contradiction"
        for item in result["critical_failures"]
    )
    assert result["qualification"] != "NEEDS_ADJUDICATION"

    sheet = _filled_sheet(submission)
    sheet["grades"][0]["claims"][0]["evidence"]["references"] = ["invented/path.py:999"]
    result = _score(tmp_path, sheet, submission, "fabricated-hard-fail.json")
    assert any(
        item.get("kind") == "fabricated_evidence"
        for item in result["critical_failures"]
    )
    assert result["qualification"] != "NEEDS_ADJUDICATION"


def test_unknown_qualification_profile_fails_closed(tmp_path: Path) -> None:
    submission = _submission(tmp_path)
    try:
        exam_module.grade_template(
            submission=submission, qualification_profile="model-brand-special.v1"
        )
    except ValueError as error:
        assert "qualification profile must be one of" in str(error)
    else:
        raise AssertionError("unknown qualification profile must fail closed")


def test_forbidden_relationship_is_non_compensatory(tmp_path: Path) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    sheet["grades"][0]["assertions"].append(
        {
            "subject": "Plan",
            "relation": "owns",
            "object": "Step execution",
            "polarity": "AFFIRMED",
        }
    )
    result = _score(tmp_path, sheet, submission, "forbidden-graph.json")
    assert "forbidden_relationship" in result["gate_failures"]
    assert any(
        item["kind"] == "forbidden_relationship" for item in result["critical_failures"]
    )
    assert not result["qualified"]


def test_question_health_audit_is_secret_and_current_bank_is_release_ready(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        QUESTION_AUDIT_POLICY_VERSION,
    )

    audit_path = ANSWERS / "question-audit.yml"
    audit = yaml.safe_load(audit_path.read_text(encoding="utf-8"))
    assert audit["policy_version"] == QUESTION_AUDIT_POLICY_VERSION
    assert len(audit["questions"]) == 220
    assert {row["health_status"] for row in audit["questions"]} == {"VALID"}
    assert {row["calibration_state"] for row in audit["questions"]} == {"UNCALIBRATED"}

    manifest = exam_module.verify()
    assert manifest["question_audit"]["release_status"] == "READY"
    assert manifest["question_audit"]["release_status_scope"] == "QUESTION_HEALTH_ONLY"
    assert (
        manifest["question_audit"]["empirical_calibration_affects_release_status"]
        is False
    )
    assert manifest["question_audit"]["empirical_calibration_status"] == "UNCALIBRATED"
    assert (
        manifest["question_audit"]["critical_empirical_calibration_status"]
        == "UNCALIBRATED"
    )
    assert manifest["question_audit"]["uncalibrated_question_count"] == 220
    assert manifest["question_audit"]["calibrated_question_count"] == 0
    assert manifest["question_audit"]["critical_question_count"] == 45
    assert manifest["question_audit"]["critical_uncalibrated_question_count"] == 45
    assert manifest["question_audit"]["critical_calibrated_question_count"] == 0
    assert manifest["question_audit"]["non_score_bearing_questions"] == []

    prepared = exam_module.prepare(tmp_path / "candidate")
    assert not (prepared / "docs/certification/answers").exists()
    assert not tuple(prepared.rglob("question-audit.yml"))


def test_question_health_ready_is_explicitly_not_an_empirical_calibration_claim() -> (
    None
):
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        audit_manifest,
    )

    exam = material.load_exam()
    qualification_ids = {
        row["id"]
        for row in exam_module.question_lifecycle()["questions"]
        if row["qualification_bank_member"]
    }
    critical_ids = tuple(
        question.id
        for question in exam
        if question.id in qualification_ids and question.severity == "critical"
    )
    report = audit_manifest(
        exam_module.question_audit(), critical_question_ids=critical_ids
    )
    assert report["release_status"] == "READY"
    assert report["release_status_scope"] == "QUESTION_HEALTH_ONLY"
    assert report["empirical_calibration_affects_release_status"] is False
    assert report["empirical_calibration_status"] == "UNCALIBRATED"
    assert report["critical_empirical_calibration_status"] == "UNCALIBRATED"
    assert report["uncalibrated_question_count"] == 220
    assert report["critical_uncalibrated_question_count"] == 45


def test_empirical_calibration_reports_difficulty_discrimination_without_policy_mutation(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    observations = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "runs": [
            {
                "system_id": "system-strong",
                "cohort": "STRONG",
                "questions": [
                    {
                        "id": "Q01",
                        "success": True,
                        "tool_calls": 4,
                        "time_to_evidence_seconds": 2.0,
                        "context_tokens": 1000,
                        "failure_mode": None,
                    }
                ],
            },
            {
                "system_id": "system-mid",
                "cohort": "MID",
                "questions": [
                    {
                        "id": "Q01",
                        "success": True,
                        "tool_calls": 8,
                        "time_to_evidence_seconds": 4.0,
                        "context_tokens": 2000,
                        "failure_mode": None,
                    }
                ],
            },
            {
                "system_id": "system-weak",
                "cohort": "WEAK",
                "questions": [
                    {
                        "id": "Q01",
                        "success": False,
                        "tool_calls": 12,
                        "time_to_evidence_seconds": 8.0,
                        "context_tokens": 3000,
                        "failure_mode": "WRONG_OWNER",
                    }
                ],
            },
        ],
    }
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    report = calibration_report(
        path,
        question_ids=authored,
        authored_difficulty=authored,
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
    )
    q1 = report["questions"]["Q01"]
    assert q1["authored_difficulty"] == authored["Q01"]
    assert q1["empirical_success_rate_percent"] == 66.7
    assert q1["median_tool_calls"] == 8.0
    assert q1["median_time_to_evidence_seconds"] == 4.0
    assert q1["median_context_tokens"] == 2000.0
    assert q1["cohort_success_rate_percent"] == {
        "STRONG": 100.0,
        "MID": 100.0,
        "WEAK": 0.0,
    }
    assert q1["item_discrimination_strong_minus_weak"] == 100.0
    assert q1["failure_mode_distribution"] == {"WRONG_OWNER": 1}
    assert q1["calibration_maturity"] == "CALIBRATING"
    assert q1["calibrated_difficulty"] is None
    assert q1["cohort_system_count"] == {"STRONG": 1, "MID": 1, "WEAK": 1}
    assert q1["review_eligibility"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert q1["review_eligibility"]["minimum_systems_per_cohort"] == 2


def test_calibration_review_eligibility_and_audit_promotion_require_retained_sufficient_evidence(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_promotion_review,
        calibration_report,
        load_question_audit,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }

    question_bindings = {
        question.id: material._calibration_question_binding(question)
        for question in material.load_exam()
    }

    def report_for(per_cohort: int) -> dict[str, Any]:
        runs = []
        for cohort in ("STRONG", "MID", "WEAK"):
            for index in range(per_cohort):
                runs.append(
                    {
                        "system_id": f"{cohort.lower()}-{index}",
                        "candidate_identity": "sha256:"
                        + __import__("hashlib")
                        .sha256(f"candidate:{cohort}:{index}".encode("utf-8"))
                        .hexdigest(),
                        "grading_artifact_identity": "sha256:"
                        + __import__("hashlib")
                        .sha256(f"grading:{cohort}:{index}".encode("utf-8"))
                        .hexdigest(),
                        "qualification_profile": "calibration-test",
                        "model_identity": "sha256:"
                        + __import__("hashlib")
                        .sha256(f"model:{cohort}:{index}".encode("utf-8"))
                        .hexdigest(),
                        "configuration_identity": "sha256:"
                        + __import__("hashlib")
                        .sha256(f"config:{cohort}:{index}".encode("utf-8"))
                        .hexdigest(),
                        "system_family": f"family-{cohort.lower()}-{index}",
                        "provider": "test-provider",
                        "cohort": cohort,
                        "questions": [
                            {
                                "id": "Q01",
                                "success": cohort != "WEAK",
                                "tool_calls": 5 + index,
                                "time_to_evidence_seconds": 2.0 + index,
                                "context_tokens": 1000 + index,
                                "failure_mode": None
                                if cohort != "WEAK"
                                else "WRONG_OWNER",
                            }
                        ],
                    }
                )
        observations = {
            "artifact_type": "repository-understanding-calibration-observations",
            "schema_version": 1,
            "source_version": snapshot["source_version"],
            "repository_identity": snapshot["repository_identity"],
            "exam_snapshot_sha256": snapshot["snapshot_sha256"],
            "runs": runs,
        }
        observations_path = tmp_path / f"observations-{per_cohort}.json"
        observations_path.write_text(json.dumps(observations), encoding="utf-8")
        return calibration_report(
            observations_path,
            question_ids=authored,
            authored_difficulty=authored,
            source_version=snapshot["source_version"],
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
            question_binding_identities=question_bindings,
        )

    insufficient = report_for(1)
    assert (
        insufficient["questions"]["Q01"]["review_eligibility"]["status"]
        == "INSUFFICIENT_EVIDENCE"
    )
    sufficient = report_for(2)
    q1 = sufficient["questions"]["Q01"]
    assert q1["observation_count"] == 6
    assert q1["system_count"] == 6
    assert q1["independent_subject_count"] == 6
    assert q1["cohort_system_count"] == {"STRONG": 2, "MID": 2, "WEAK": 2}
    assert q1["cohort_independent_subject_count"] == {"STRONG": 2, "MID": 2, "WEAK": 2}
    assert q1["review_eligibility"]["status"] == "ELIGIBLE_FOR_REVIEW"
    assessment = q1["promotion_evidence_assessment"]
    assert assessment["status"] == "READY_FOR_HUMAN_REVIEW"
    assert assessment["automatic_promotion"] is False
    assert assessment["overall_success_interval"]["method"] == "WILSON_SCORE"
    assert assessment["overall_success_interval"]["confidence_level"] == 0.95
    assert assessment["cohort_success_intervals"]["STRONG"]["total"] == 2
    assert assessment["cohort_success_intervals"]["WEAK"]["successes"] == 0
    assert assessment["assessment_identity"].startswith("sha256:")
    assert q1["calibration_maturity"] == "CALIBRATING"
    assert q1["calibrated_difficulty"] is None

    audit = yaml.safe_load((ANSWERS / "question-audit.yml").read_text(encoding="utf-8"))
    audit_path = tmp_path / "question-audit.yml"
    qrow = audit["questions"][0]
    qrow["calibration_state"] = "CALIBRATED"
    qrow["calibrated_difficulty"] = authored["Q01"]
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="requires calibration_evidence"):
        load_question_audit(
            audit_path,
            source_version=snapshot["source_version"],
            question_ids=_question_ids(),
        )

    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir()
    insufficient_path = calibration_dir / "q01.json"
    insufficient_path.write_text(json.dumps(insufficient), encoding="utf-8")
    qrow["calibration_state"] = "CALIBRATING"
    qrow["calibrated_difficulty"] = None
    qrow["calibration_evidence"] = {
        "report_path": "calibration/q01.json",
        "report_identity": insufficient["report_identity"],
        "promotion_review_path": None,
        "promotion_review_identity": None,
    }
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    calibrating = load_question_audit(
        audit_path,
        source_version=snapshot["source_version"],
        question_ids=_question_ids(),
    )
    assert calibrating["questions"][0]["calibration_state"] == "CALIBRATING"

    qrow["calibration_state"] = "CALIBRATED"
    qrow["calibrated_difficulty"] = authored["Q01"]
    qrow["calibration_evidence"]["promotion_review_path"] = (
        "calibration/q01-review.json"
    )
    qrow["calibration_evidence"]["promotion_review_identity"] = "sha256:" + "1" * 64
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="insufficient empirical calibration evidence"):
        load_question_audit(
            audit_path,
            source_version=snapshot["source_version"],
            question_ids=_question_ids(),
        )

    sufficient_path = calibration_dir / "q01.json"
    sufficient_path.write_text(json.dumps(sufficient), encoding="utf-8")
    qrow["calibration_evidence"]["report_identity"] = sufficient["report_identity"]
    review = calibration_promotion_review(
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
        question_id="Q01",
        calibration_report_identity=sufficient["report_identity"],
        calibrated_difficulty=authored["Q01"],
        reviewer_identity="sha256:" + "2" * 64,
        evidence_assessment_identity=q1["promotion_evidence_assessment"][
            "assessment_identity"
        ],
    )
    assert review["evidence_assessment_identity"] == assessment["assessment_identity"]
    assert review["decision_basis"] == "HUMAN_REVIEW_OF_EMPIRICAL_EVIDENCE"
    review_path = calibration_dir / "q01-review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    qrow["calibration_evidence"]["promotion_review_identity"] = review[
        "review_identity"
    ]
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    calibrated = load_question_audit(
        audit_path,
        source_version=snapshot["source_version"],
        question_ids=_question_ids(),
    )
    normalized = calibrated["questions"][0]
    assert normalized["calibration_state"] == "CALIBRATED"
    assert normalized["calibrated_difficulty"] == authored["Q01"]
    assert (
        normalized["calibration_evidence"]["report_identity"]
        == sufficient["report_identity"]
    )
    assert (
        normalized["calibration_evidence"]["promotion_review_identity"]
        == review["review_identity"]
    )

    current = load_question_audit(
        audit_path,
        source_version=snapshot["source_version"],
        question_ids=_question_ids(),
        current_question_bindings=question_bindings,
    )
    assert current["questions"][0]["calibration_state"] == "CALIBRATED"
    stale_bindings = dict(question_bindings)
    stale_bindings["Q01"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="stale for current question binding"):
        load_question_audit(
            audit_path,
            source_version=snapshot["source_version"],
            question_ids=_question_ids(),
            current_question_bindings=stale_bindings,
        )

    wrong_assessment = dict(review)
    wrong_assessment["evidence_assessment_identity"] = "sha256:" + "4" * 64
    wrong_assessment["review_identity"] = (
        "sha256:"
        + __import__("hashlib")
        .sha256(
            json.dumps(
                {
                    key: value
                    for key, value in wrong_assessment.items()
                    if key != "review_identity"
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
    )
    review_path.write_text(json.dumps(wrong_assessment), encoding="utf-8")
    qrow["calibration_evidence"]["promotion_review_identity"] = wrong_assessment[
        "review_identity"
    ]
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        ValueError, match="promotion review evidence_assessment_identity does not match"
    ):
        load_question_audit(
            audit_path,
            source_version=snapshot["source_version"],
            question_ids=_question_ids(),
        )

    wrong_question = dict(review)
    wrong_question["question_id"] = "Q02"
    wrong_question["review_identity"] = (
        "sha256:"
        + __import__("hashlib")
        .sha256(
            json.dumps(
                {
                    key: value
                    for key, value in wrong_question.items()
                    if key != "review_identity"
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
    )
    review_path.write_text(json.dumps(wrong_question), encoding="utf-8")
    qrow["calibration_evidence"]["promotion_review_identity"] = wrong_question[
        "review_identity"
    ]
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="promotion review question_id does not match"):
        load_question_audit(
            audit_path,
            source_version=snapshot["source_version"],
            question_ids=_question_ids(),
        )

    wrong_report = dict(review)
    wrong_report["calibration_report_identity"] = "sha256:" + "3" * 64
    wrong_report["review_identity"] = (
        "sha256:"
        + __import__("hashlib")
        .sha256(
            json.dumps(
                {
                    key: value
                    for key, value in wrong_report.items()
                    if key != "review_identity"
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
    )
    review_path.write_text(json.dumps(wrong_report), encoding="utf-8")
    qrow["calibration_evidence"]["promotion_review_identity"] = wrong_report[
        "review_identity"
    ]
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        ValueError, match="promotion review calibration_report_identity does not match"
    ):
        load_question_audit(
            audit_path,
            source_version=snapshot["source_version"],
            question_ids=_question_ids(),
        )

    wrong_source = dict(review)
    wrong_source["source_version"] = "0.0.0"
    wrong_source["review_identity"] = (
        "sha256:"
        + __import__("hashlib")
        .sha256(
            json.dumps(
                {
                    key: value
                    for key, value in wrong_source.items()
                    if key != "review_identity"
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
    )
    review_path.write_text(json.dumps(wrong_source), encoding="utf-8")
    qrow["calibration_evidence"]["promotion_review_identity"] = wrong_source[
        "review_identity"
    ]
    audit_path.write_text(yaml.safe_dump(audit, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        ValueError, match="promotion review source_version does not match"
    ):
        load_question_audit(
            audit_path,
            source_version=snapshot["source_version"],
            question_ids=_question_ids(),
        )


def test_calibration_system_identity_cannot_cross_cohort_boundaries(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    observations = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "runs": [
            {
                "system_id": "same-system",
                "cohort": "STRONG",
                "questions": [{"id": "Q01", "success": True}],
            },
            {
                "system_id": "same-system",
                "cohort": "WEAK",
                "questions": [{"id": "Q01", "success": False}],
            },
        ],
    }
    path = tmp_path / "cross-cohort.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot appear in multiple cohorts"):
        calibration_report(
            path,
            question_ids=authored,
            authored_difficulty=authored,
            source_version=snapshot["source_version"],
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
        )


def test_calibration_alias_system_ids_do_not_fake_independent_subject_floor(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    import hashlib

    runs = []
    for cohort in ("STRONG", "MID", "WEAK"):
        model = "sha256:" + hashlib.sha256(f"same-model-{cohort}".encode()).hexdigest()
        config = (
            "sha256:" + hashlib.sha256(f"same-config-{cohort}".encode()).hexdigest()
        )
        for index in range(2):
            runs.append(
                {
                    "system_id": f"alias-{cohort}-{index}",
                    "candidate_identity": "sha256:"
                    + hashlib.sha256(
                        f"candidate-{cohort}-{index}".encode()
                    ).hexdigest(),
                    "grading_artifact_identity": "sha256:"
                    + hashlib.sha256(f"grading-{cohort}-{index}".encode()).hexdigest(),
                    "qualification_profile": "calibration-test",
                    "model_identity": model,
                    "configuration_identity": config,
                    "cohort": cohort,
                    "questions": [{"id": "Q01", "success": cohort != "WEAK"}],
                }
            )
    path = tmp_path / "aliases.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "repository-understanding-calibration-observations",
                "schema_version": 1,
                "source_version": snapshot["source_version"],
                "repository_identity": snapshot["repository_identity"],
                "exam_snapshot_sha256": snapshot["snapshot_sha256"],
                "runs": runs,
            }
        ),
        encoding="utf-8",
    )
    report = calibration_report(
        path,
        question_ids=authored,
        authored_difficulty=authored,
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
    )
    q1 = report["questions"]["Q01"]
    assert q1["system_count"] == 6
    assert q1["independent_subject_count"] == 3
    assert q1["cohort_independent_subject_count"] == {"STRONG": 1, "MID": 1, "WEAK": 1}
    assert q1["review_eligibility"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert "independent_subject_count<6" in q1["review_eligibility"]["gaps"]


def test_calibration_independent_subject_cannot_cross_cohort_aliases(
    tmp_path: Path,
) -> None:
    import hashlib
    import pytest
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    model = "sha256:" + hashlib.sha256(b"same-model").hexdigest()
    config = "sha256:" + hashlib.sha256(b"same-config").hexdigest()
    payload = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "runs": [
            {
                "system_id": "alias-a",
                "model_identity": model,
                "configuration_identity": config,
                "cohort": "STRONG",
                "questions": [{"id": "Q01", "success": True}],
            },
            {
                "system_id": "alias-b",
                "model_identity": model,
                "configuration_identity": config,
                "cohort": "WEAK",
                "questions": [{"id": "Q01", "success": False}],
            },
        ],
    }
    path = tmp_path / "cross-subject-cohort.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        ValueError, match="independent subject cannot appear in multiple cohorts"
    ):
        calibration_report(
            path,
            question_ids=authored,
            authored_difficulty=authored,
            source_version=snapshot["source_version"],
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
        )


def test_calibration_telemetry_is_bound_to_exact_frozen_exam_identity(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    observations = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": "sha256:wrong",
        "runs": [{"system_id": "system-a", "cohort": "MID", "questions": []}],
    }
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ValueError, match="exam snapshot identity mismatch"):
        calibration_report(
            path,
            question_ids=authored,
            authored_difficulty=authored,
            source_version=snapshot["source_version"],
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
        )


def test_known_bad_active_question_blocks_bank_release() -> None:
    import copy
    import pytest
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        assert_question_audit_release_ready,
        load_question_audit,
    )

    source_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    audit = load_question_audit(
        ANSWERS / "question-audit.yml",
        source_version=source_version,
        question_ids=_question_ids(),
    )
    bad = copy.deepcopy(audit)
    bad["questions"][0]["health_status"] = "AMBIGUOUS"
    with pytest.raises(ValueError, match="non-score-bearing question health states"):
        assert_question_audit_release_ready(bad)


def test_candidate_confidence_and_abstention_are_explicit_grader_observations(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = exam_module.grade_template(submission=submission)
    claim = sheet["grades"][0]["claims"][0]
    assert "candidate_epistemic_state" in claim
    assert "candidate_confidence" in claim
    assert claim["candidate_epistemic_state"] is None
    assert claim["candidate_confidence"] is None
    manifest = exam_module.verify()
    assert manifest["candidate_confidence_states"] == ["HIGH", "MEDIUM", "LOW"]
    assert manifest["candidate_epistemic_states"] == ["ASSERTED", "ABSTAINED"]


def test_correct_abstention_is_epistemically_visible_without_semantic_credit(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    claim = sheet["grades"][0]["claims"][0]
    claim["result"] = "INSUFFICIENT_EVIDENCE"
    claim["rationale"] = (
        "Candidate explicitly states that the available evidence is insufficient."
    )
    claim["candidate_epistemic_state"] = "ABSTAINED"
    claim["candidate_confidence"] = "LOW"
    result = _score(tmp_path, sheet, submission, "abstention.json")
    epistemic = result["confidence_calibration"]["candidate_epistemic"]
    assert "Q01/concept" in epistemic["accepted_abstentions"]
    assert epistemic["critical_overconfidence"] == []
    assert result["questions"][0]["claims"][0]["result"] == "INSUFFICIENT_EVIDENCE"
    assert (
        result["questions"][0]["claims"][0]["candidate_epistemic_state"] == "ABSTAINED"
    )
    assert result["questions"][0]["score"] < 100.0


def test_write_authority_profile_records_critical_overconfidence_as_gate_failure(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    snapshot = {item["id"]: item for item in sheet["exam_snapshot"]["questions"]}
    target_grade = next(
        grade
        for grade in sheet["grades"]
        if any(
            claim["critical"]
            for claim in snapshot[grade["id"]]["secret"]["expected_claims"]
        )
    )
    target_question = snapshot[target_grade["id"]]
    critical_id = next(
        claim["id"]
        for claim in target_question["secret"]["expected_claims"]
        if claim["critical"]
    )
    claim = next(
        item for item in target_grade["claims"] if item["claim_id"] == critical_id
    )
    claim["result"] = "UNSUPPORTED"
    claim["rationale"] = "Candidate asserts a wrong critical fact."
    claim["candidate_epistemic_state"] = "ASSERTED"
    claim["candidate_confidence"] = "HIGH"
    result = _score(tmp_path, sheet, submission, "critical-overconfidence.json")
    epistemic = result["confidence_calibration"]["candidate_epistemic"]
    assert f"{target_grade['id']}/{critical_id}" in epistemic["critical_overconfidence"]
    assert "critical_overconfidence" in result["gate_failures"]
    assert not result["qualified"]


def test_partial_critical_claim_with_high_candidate_confidence_is_overconfidence(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = _filled_sheet(submission)
    snapshot = {item["id"]: item for item in sheet["exam_snapshot"]["questions"]}
    target_grade = next(
        grade
        for grade in sheet["grades"]
        if any(
            claim["critical"]
            for claim in snapshot[grade["id"]]["secret"]["expected_claims"]
        )
    )
    target_question = snapshot[target_grade["id"]]
    critical_id = next(
        claim["id"]
        for claim in target_question["secret"]["expected_claims"]
        if claim["critical"]
    )
    claim = next(
        item for item in target_grade["claims"] if item["claim_id"] == critical_id
    )
    claim["result"] = "PARTIAL"
    claim["rationale"] = "Candidate established only part of the critical claim."
    claim["candidate_confidence"] = "HIGH"
    result = _score(tmp_path, sheet, submission, "partial-critical-overconfidence.json")
    epistemic = result["confidence_calibration"]["candidate_epistemic"]
    assert f"{target_grade['id']}/{critical_id}" in epistemic["critical_overconfidence"]
    assert "critical_overconfidence" in result["gate_failures"]
    assert not result["qualified"]


def _candidate_configuration(
    tmp_path: Path, name: str = "candidate-config.json"
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "model": "candidate-a",
                "harness": "oh-goon-repository-understanding",
                "temperature": 0.2,
                "tool_policy": "read-only",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _stable_result(
    tmp_path: Path,
    index: int,
    config: Path,
    *,
    partial_critical: bool = False,
    profile: str = "change-author.v1",
) -> Path:
    submission = tmp_path / f"candidate-run-{index}.md"
    submission.write_text(f"run {index} candidate answers\n", encoding="utf-8")
    sheet = exam_module.grade_template(
        submission=submission,
        candidate="candidate-a",
        mode="normal",
        candidate_configuration=config,
        qualification_profile=profile,
    )
    sheet["grader"] = {
        "type": "llm",
        "identity": "grader-a",
        "rubric_version": material.RUBRIC_VERSION,
        "configuration": {"temperature": 0},
    }
    sheet["graded_at_utc"] = f"2026-09-05T10:0{index}:00Z"
    sheet["candidate_run_identity"] = f"candidate-run-{index}"
    snapshot = {item["id"]: item for item in sheet["exam_snapshot"]["questions"]}
    for grade in sheet["grades"]:
        question = snapshot[grade["id"]]
        canonical_evidence = question["secret"]["evidence_authority_roles"][
            "required_authority"
        ]
        assert canonical_evidence
        canonical_reference = canonical_evidence[0]["reference"]
        grade["question_status"] = "QUESTION_VALID"
        grade["reference_status"] = "REFERENCE_SUPPORTED"
        for claim in grade["claims"]:
            claim["result"] = "SUPPORTED"
            claim["confidence"] = 1.0
            claim["candidate_epistemic_state"] = "ASSERTED"
            claim["candidate_confidence"] = "HIGH"
            claim["evidence"] = {
                "references": [canonical_reference],
                "relevance": "STRONG",
                "support": "STRONG",
                "authority": "AUTHORITATIVE",
            }
            claim["rationale"] = None
        for contradiction in grade["contradictions"]:
            contradiction["observed"] = False
            contradiction["confidence"] = 1.0
            contradiction["rationale"] = None
        grade["assertions"] = []
        grade["rationale"] = None
    for grade, edge in zip(sheet["grades"], EXPECTED_EDGES, strict=False):
        grade["assertions"] = [
            {
                "subject": edge["subject"],
                "relation": edge["relation"],
                "object": edge["object"],
                "polarity": "AFFIRMED",
            }
        ]
    if partial_critical:
        for grade in sheet["grades"]:
            question = snapshot[grade["id"]]
            critical = [
                claim
                for claim in question["secret"]["expected_claims"]
                if claim["critical"]
            ]
            if not critical:
                continue
            target = next(
                item
                for item in grade["claims"]
                if item["claim_id"] == critical[0]["id"]
            )
            target["result"] = "PARTIAL"
            target["rationale"] = "Run recovered only part of this critical concept."
            target["candidate_confidence"] = "MEDIUM"
            break
    grades = tmp_path / f"run-{index}-grades.json"
    grades.write_text(json.dumps(sheet), encoding="utf-8")
    result = exam_module.score(grades, submission=submission)
    output = tmp_path / f"run-{index}-result.json"
    output.write_text(json.dumps(result), encoding="utf-8")
    return output


def test_repeated_run_stability_requires_three_distinct_identity_bound_passes(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_stability import (
        stability_report,
    )

    config = _candidate_configuration(tmp_path)
    results = [_stable_result(tmp_path, index, config) for index in (1, 2, 3)]
    report = stability_report(results)
    assert report["runs_required"] == 3
    assert report["passes_required"] == 3
    assert report["passes"] == 3
    assert report["qualification_rate_percent"] == 100.0
    assert report["claim_agreement_rate_percent"] == 100.0
    assert report["concept_agreement_rate_percent"] == 100.0
    assert report["critical_concept_oscillations"] == []
    assert report["stability"] == "STABLE"
    assert report["qualified"] is True
    assert report["stability_certificate_identity"].startswith("sha256:")


def test_repeated_run_stability_rejects_lucky_global_pass_with_critical_concept_oscillation(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_stability import (
        stability_report,
    )

    config = _candidate_configuration(tmp_path)
    results = [
        _stable_result(tmp_path, 1, config),
        _stable_result(tmp_path, 2, config, partial_critical=True),
        _stable_result(tmp_path, 3, config),
    ]
    scored = [json.loads(path.read_text(encoding="utf-8")) for path in results]
    assert all(result["qualified"] for result in scored)
    report = stability_report(results)
    assert report["passes"] == 3
    assert report["critical_concept_oscillations"]
    assert "critical_concept_oscillation" in report["stability_gate_failures"]
    assert report["qualified"] is False


def test_repeated_run_stability_rejects_mixed_candidate_configuration_identity(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_stability import (
        stability_report,
    )

    config_a = _candidate_configuration(tmp_path, "config-a.json")
    config_b = tmp_path / "config-b.json"
    config_b.write_text(
        json.dumps({"model": "candidate-b", "harness": "other"}), encoding="utf-8"
    )
    results = [
        _stable_result(tmp_path, 1, config_a),
        _stable_result(tmp_path, 2, config_a),
        _stable_result(tmp_path, 3, config_b),
    ]
    with pytest.raises(ValueError, match="candidate_configuration_identity"):
        stability_report(results)


def test_repeated_run_stability_rejects_duplicate_candidate_run_identity(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_stability import (
        stability_report,
    )

    config = _candidate_configuration(tmp_path)
    results = [_stable_result(tmp_path, index, config) for index in (1, 2, 3)]
    third = json.loads(results[2].read_text(encoding="utf-8"))
    third["candidate_run_identity"] = "candidate-run-2"
    third["regrade_bundle"]["candidate_run_identity"] = "candidate-run-2"
    third["certificate_identity"] = "sha256:" + material._canonical_json_sha256(
        {
            key: value
            for key, value in third.items()
            if key not in {"certificate_identity", "regrade_bundle", "regraded"}
        }
    )
    results[2].write_text(json.dumps(third), encoding="utf-8")
    with pytest.raises(ValueError, match="distinct candidate run identities"):
        stability_report(results)


def test_repeated_run_stability_rejects_tampered_result_certificate_identity(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_stability import (
        stability_report,
    )

    config = _candidate_configuration(tmp_path)
    results = [_stable_result(tmp_path, index, config) for index in (1, 2, 3)]
    tampered = json.loads(results[1].read_text(encoding="utf-8"))
    tampered["composite_understanding"] = 99.9
    results[1].write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(
        ValueError, match="certificate identity does not match result bytes"
    ):
        stability_report(results)


def _challenge_instance(
    tmp_path: Path, seed: str = "seed-a"
) -> tuple[dict[str, Any], Path]:
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        challenge_set,
    )

    snapshot = material.exam_snapshot()
    payload = challenge_set(
        exam_module.benchmark_registry(),
        questions=material.load_exam(),
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
        seed=seed,
    )
    path = tmp_path / f"challenge-{seed}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload, path


def _adversarial_observations(challenge: dict[str, Any]) -> dict[str, Any]:
    registry = exam_module.benchmark_registry()
    challenges = []
    for binding in challenge["secret_bindings"]:
        role = binding["role"]
        challenges.append(
            {
                "delivery_id": binding["delivery_id"],
                "outcome": (
                    "EVIDENCE_BACKED_RESISTANCE"
                    if role == "CONTRADICTION_PRESSURE"
                    else "CONSISTENT"
                ),
                "rationale": "Evidence agrees with the canonical concept.",
            }
        )
    return {
        "artifact_type": "repository-understanding-adversarial-observations",
        "schema_version": 1,
        "source_version": challenge["source_version"],
        "repository_identity": challenge["repository_identity"],
        "exam_snapshot_sha256": challenge["exam_snapshot_sha256"],
        "benchmark_registry_identity": challenge["benchmark_registry_identity"],
        "challenge_instance_identity": challenge["challenge_instance_identity"],
        "challenges": challenges,
        "false_premises": [
            {"question_id": qid, "premise_response": "REJECTED"}
            for qid in registry["false_premise_question_ids"]
        ],
    }


def test_benchmark_integrity_registry_is_secret_prompt_free_in_public_manifest() -> (
    None
):
    manifest = material.manifest()
    integrity = manifest["benchmark_integrity"]
    assert integrity["status"] == "READY"
    assert integrity["false_premise_questions"] == 19
    assert integrity["equivalence_families"] == 6
    assert integrity["hidden_paraphrases"] == 12
    assert integrity["contradiction_pressure_challenges"] == 4
    assert integrity["canary_questions"] == 3
    assert integrity["fresh_shadow_questions"] == 2
    assert integrity["canary_slots_per_challenge_set"] == 1
    assert integrity["fresh_shadow_slots_per_challenge_set"] == 1
    assert integrity["rotation_strategy"] == "DETERMINISTIC_SEEDED_WITHOUT_REPLACEMENT"
    assert integrity["canaries_score_bearing"] is False
    assert integrity["fresh_shadow_score_bearing"] is False
    rendered = json.dumps(integrity)
    assert "PRESS-001" not in rendered
    assert "CANARY-001" not in rendered
    assert "The UI already has the run identity" not in rendered


def test_prepared_candidate_workspace_excludes_hidden_benchmark_registry_and_git_history(
    tmp_path: Path,
) -> None:
    prepared = exam_module.prepare(tmp_path / "candidate")
    assert not (prepared / "docs/certification/answers").exists()
    assert not (prepared / ".git").exists()
    marker = json.loads(
        (prepared / ".oh-goon-certification-exam.json").read_text(encoding="utf-8")
    )
    controls = marker["benchmark_integrity_contract"]
    assert controls["network_access"] == "DENIED"
    assert controls["prior_conversation_history"] == "DENIED"
    assert controls["prior_candidate_answers"] == "DENIED"
    assert controls["grader_secret_material"] == "DENIED"
    assert controls["candidate_workspace_mutation"] == "DENIED"


def test_exam_snapshot_freezes_exact_secret_evidence_range_bytes() -> None:
    snapshot = material.exam_snapshot()
    frozen = [
        identity
        for question in snapshot["questions"]
        for identity in question["secret"]["evidence_reference_identities"]
    ]
    assert frozen
    assert all(item["source_sha256"].startswith("sha256:") for item in frozen)
    assert all(item["range_sha256"].startswith("sha256:") for item in frozen)
    assert all(item["end_line"] >= item["start_line"] >= 1 for item in frozen)


def test_challenge_set_selects_one_hidden_paraphrase_per_family_and_hides_secret_roles(
    tmp_path: Path,
) -> None:
    first, _ = _challenge_instance(tmp_path, "same-seed")
    second, _ = _challenge_instance(tmp_path, "same-seed")
    assert first == second
    assert first["challenge_instance_identity"].startswith("sha256:")
    bindings = first["secret_bindings"]
    assert sum(item["role"] == "PARAPHRASE" for item in bindings) == 6
    assert sum(item["role"] == "CONTRADICTION_PRESSURE" for item in bindings) == 4
    assert sum(item["role"] == "CANARY" for item in bindings) == 1
    assert sum(item["role"] == "FRESH_SHADOW" for item in bindings) == 1
    assert first["rotation"]["canary_pool_size"] == 3
    assert first["rotation"]["canary_slots"] == 1
    assert first["rotation"]["fresh_shadow_pool_size"] == 2
    assert first["rotation"]["fresh_shadow_slots"] == 1
    public_rendered = json.dumps(first["public_delivery"])
    assert "question_id" not in public_rendered
    assert "CANARY" not in public_rendered
    assert "FRESH_SHADOW" not in public_rendered
    assert "secret_bindings" not in public_rendered


def test_adversarial_report_keeps_rates_diagnostic_but_vetoes_failed_critical_challenge(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        adversarial_report,
    )

    challenge, challenge_path = _challenge_instance(tmp_path)
    observations = _adversarial_observations(challenge)
    path = tmp_path / "adversarial.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    clean = adversarial_report(
        challenge_path, path, registry=exam_module.benchmark_registry()
    )
    assert clean["qualified"] is True
    assert clean["paraphrase_consistency_percent"] == 100.0
    assert clean["pressure_resistance_percent"] == 100.0
    assert clean["false_premise_rejection_percent"] == 100.0
    assert clean["critical_adversarial_failures"] == []

    critical_pressure = next(
        binding
        for binding in challenge["secret_bindings"]
        if binding["critical"] and binding["role"] == "CONTRADICTION_PRESSURE"
    )
    target = next(
        row
        for row in observations["challenges"]
        if row["delivery_id"] == critical_pressure["delivery_id"]
    )
    target["outcome"] = "UNSUPPORTED_YIELD"
    path.write_text(json.dumps(observations), encoding="utf-8")
    failed = adversarial_report(
        challenge_path, path, registry=exam_module.benchmark_registry()
    )
    assert failed["qualified"] is False
    assert failed["critical_adversarial_failures"]
    assert failed["adversarial_gate_failures"] == ["critical_adversarial_failure"]


def test_adversarial_report_rejects_incomplete_false_premise_coverage(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        adversarial_report,
    )

    challenge, challenge_path = _challenge_instance(tmp_path)
    observations = _adversarial_observations(challenge)
    observations["false_premises"].pop()
    path = tmp_path / "adversarial-incomplete.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ValueError, match="every canonical false-premise"):
        adversarial_report(
            challenge_path, path, registry=exam_module.benchmark_registry()
        )


def _benchmark_execution_evidence(challenge: dict[str, Any]) -> dict[str, Any]:
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        BENCHMARK_CONTROL_REQUIREMENTS,
    )

    return {
        "artifact_type": "repository-understanding-benchmark-execution-evidence",
        "schema_version": 1,
        "source_version": challenge["source_version"],
        "repository_identity": challenge["repository_identity"],
        "exam_snapshot_sha256": challenge["exam_snapshot_sha256"],
        "benchmark_registry_identity": challenge["benchmark_registry_identity"],
        "challenge_instance_identity": challenge["challenge_instance_identity"],
        "candidate_run_identity": "candidate-run-integrity-1",
        "candidate_configuration_identity": "sha256:" + "1" * 64,
        "controls": dict(BENCHMARK_CONTROL_REQUIREMENTS),
    }


def test_benchmark_integrity_requires_no_network_history_answers_or_secret_access(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        benchmark_integrity_report,
    )

    challenge, challenge_path = _challenge_instance(tmp_path)
    evidence = _benchmark_execution_evidence(challenge)
    evidence_path = tmp_path / "execution-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = benchmark_integrity_report(
        evidence_path,
        registry=exam_module.benchmark_registry(),
        source_version=challenge["source_version"],
        repository_identity=challenge["repository_identity"],
        exam_snapshot_sha256=challenge["exam_snapshot_sha256"],
        challenge_instance_path=challenge_path,
    )
    assert report["comparison_status"] == "COMPARABLE"
    assert report["control_failures"] == []

    evidence["controls"]["network_access"] = "ALLOWED"
    evidence["controls"]["prior_conversation_history"] = "ALLOWED"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report = benchmark_integrity_report(
        evidence_path,
        registry=exam_module.benchmark_registry(),
        source_version=challenge["source_version"],
        repository_identity=challenge["repository_identity"],
        exam_snapshot_sha256=challenge["exam_snapshot_sha256"],
        challenge_instance_path=challenge_path,
    )
    assert report["comparison_status"] == "NOT_COMPARABLE"
    assert report["control_failures"] == [
        "network_access",
        "prior_conversation_history",
    ]


def test_benchmark_integrity_rejects_tampered_challenge_identity(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        benchmark_integrity_report,
    )

    challenge, challenge_path = _challenge_instance(tmp_path)
    evidence = _benchmark_execution_evidence(challenge)
    evidence_path = tmp_path / "execution-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    tampered = json.loads(challenge_path.read_text(encoding="utf-8"))
    tampered["public_delivery"][0]["prompt"] += " tampered"
    challenge_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="identity does not match challenge bytes"):
        benchmark_integrity_report(
            evidence_path,
            registry=exam_module.benchmark_registry(),
            source_version=challenge["source_version"],
            repository_identity=challenge["repository_identity"],
            exam_snapshot_sha256=challenge["exam_snapshot_sha256"],
            challenge_instance_path=challenge_path,
        )


def test_canary_registry_rejects_score_bearing_or_calibrated_canary(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        load_benchmark_registry,
    )

    source = yaml.safe_load(
        (ANSWERS / "benchmark-integrity.yml").read_text(encoding="utf-8")
    )
    source["canaries"][0]["score_bearing"] = True
    bad = tmp_path / "bad-integrity.yml"
    bad.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="NEVER calibrated and non-score-bearing"):
        load_benchmark_registry(
            bad,
            source_version=(ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            questions=material.load_exam(),
        )


def test_canary_introduction_version_is_historical_and_not_rewritten(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        load_benchmark_registry,
    )

    source = yaml.safe_load(
        (ANSWERS / "benchmark-integrity.yml").read_text(encoding="utf-8")
    )
    source["canaries"][0]["introduced_source_version"] = "1267.0.71"
    source["fresh_challenges"][0]["introduced_source_version"] = "1267.0.71"
    historical = tmp_path / "historical-integrity.yml"
    historical.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    registry = load_benchmark_registry(
        historical,
        source_version=(ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        questions=material.load_exam(),
    )
    assert registry["canaries"][0]["introduced_source_version"] == "1267.0.71"
    assert registry["fresh_challenges"][0]["introduced_source_version"] == "1267.0.71"


def test_canary_introduction_version_rejects_future_identity(tmp_path: Path) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        load_benchmark_registry,
    )

    source = yaml.safe_load(
        (ANSWERS / "benchmark-integrity.yml").read_text(encoding="utf-8")
    )
    current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    major, minor, patch = (int(part) for part in current.split("."))
    source["canaries"][0]["introduced_source_version"] = f"{major}.{minor}.{patch + 1}"
    bad = tmp_path / "future-integrity.yml"
    bad.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be in the future"):
        load_benchmark_registry(
            bad,
            source_version=(ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            questions=material.load_exam(),
        )


def test_challenge_registry_binding_rejects_tamper_even_after_self_hash_recomputed(
    tmp_path: Path,
) -> None:
    import hashlib
    import pytest
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        benchmark_integrity_report,
    )

    challenge, challenge_path = _challenge_instance(tmp_path)
    evidence = _benchmark_execution_evidence(challenge)
    tampered = json.loads(challenge_path.read_text(encoding="utf-8"))
    tampered["secret_bindings"][0]["critical"] = not tampered["secret_bindings"][0][
        "critical"
    ]
    body = {
        key: value
        for key, value in tampered.items()
        if key != "challenge_instance_identity"
    }
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    tampered["challenge_instance_identity"] = (
        "sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    challenge_path.write_text(json.dumps(tampered), encoding="utf-8")
    evidence["challenge_instance_identity"] = tampered["challenge_instance_identity"]
    evidence_path = tmp_path / "execution-evidence-rehashed-tamper.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="drifted from SECRET registry"):
        benchmark_integrity_report(
            evidence_path,
            registry=exam_module.benchmark_registry(),
            source_version=challenge["source_version"],
            repository_identity=challenge["repository_identity"],
            exam_snapshot_sha256=challenge["exam_snapshot_sha256"],
            challenge_instance_path=challenge_path,
        )


def test_question_lifecycle_is_secret_explicit_and_current_bank_is_active(
    tmp_path: Path,
) -> None:
    lifecycle = exam_module.question_lifecycle()
    assert lifecycle["policy_version"] == "repository-question-lifecycle.v3"
    assert len(lifecycle["questions"]) == 220
    active = [row for row in lifecycle["questions"] if row["qualification_bank_member"]]
    shadow = [
        row for row in lifecycle["questions"] if not row["qualification_bank_member"]
    ]
    assert len(active) == 157 and all(row["state"] == "ACTIVE" for row in active)
    assert len(shadow) == 63 and all(row["state"] == "SHADOW" for row in shadow)
    assert all(row["score_bearing"] is True for row in active)
    assert all(row["score_bearing"] is False for row in shadow)
    assert all(
        row["activation_basis"] == "LEGACY_ACTIVE"
        for row in active
        if int(row["id"][1:]) <= 147
    )
    assert all(
        row["activation_basis"] == "PROMOTION_REVIEW"
        for row in active
        if int(row["id"][1:]) >= 203
    )
    manifest = exam_module.verify()
    assert manifest["question_lifecycle"]["states"]["ACTIVE"] == 157
    assert manifest["question_lifecycle"]["states"]["SHADOW"] == 63
    assert manifest["question_lifecycle"]["qualification_bank_count"] == 157
    assert manifest["question_lifecycle"]["shadow_pool_count"] == 63
    assert manifest["question_lifecycle"]["score_bearing_count"] == 157
    assert manifest["question_lifecycle"]["qualification_status"] == "READY"
    assert manifest["question_lifecycle"]["unavailable_critical_question_count"] == 0
    prepared = exam_module.prepare(tmp_path / "candidate-lifecycle")
    assert not tuple(prepared.rglob("question-lifecycle.yml"))


def test_question_lifecycle_review_required_blocks_critical_qualification_without_threshold_rescale(
    tmp_path: Path,
) -> None:
    import copy
    import pytest
    from oh_goon.repository_tooling.repository_understanding_lifecycle import (
        assert_current_score_bank_active,
        load_question_lifecycle,
        question_lifecycle_manifest,
    )

    snapshot = material.exam_snapshot()
    critical_id = next(
        question["id"]
        for question in snapshot["questions"]
        if question["severity"] == "critical"
    )
    payload = yaml.safe_load(
        (ANSWERS / "question-lifecycle.yml").read_text(encoding="utf-8")
    )
    row = next(item for item in payload["questions"] if item["id"] == critical_id)
    row.update(
        {
            "state": "REVIEW_REQUIRED",
            "state_since_source_version": snapshot["source_version"],
            "score_bearing": False,
            "candidate_delivery": False,
            "delivery_mode": "WITHHELD_REVIEW_REQUIRED",
            "activation_basis": None,
        }
    )
    path = tmp_path / "question-lifecycle.yml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    lifecycle = load_question_lifecycle(
        path, source_version=snapshot["source_version"], question_ids=_question_ids()
    )
    report = question_lifecycle_manifest(lifecycle, critical_question_ids=[critical_id])
    assert report["qualification_status"] == "BLOCKED"
    assert report["unavailable_question_count"] == 1
    assert report["unavailable_critical_question_ids"] == [critical_id]
    assert report["score_bearing_count"] == 156
    with pytest.raises(ValueError, match="qualification thresholds are not rescaled"):
        assert_current_score_bank_active(lifecycle, critical_question_ids=[critical_id])

    quarantined = copy.deepcopy(payload)
    qrow = next(item for item in quarantined["questions"] if item["id"] == critical_id)
    qrow["state"] = "QUARANTINED"
    qrow["delivery_mode"] = "WITHHELD_QUARANTINED"
    path.write_text(yaml.safe_dump(quarantined, sort_keys=False), encoding="utf-8")
    loaded = load_question_lifecycle(
        path, source_version=snapshot["source_version"], question_ids=_question_ids()
    )
    assert (
        question_lifecycle_manifest(loaded, critical_question_ids=[critical_id])[
            "qualification_status"
        ]
        == "BLOCKED"
    )


def test_human_baseline_reports_timing_and_disagreement_without_mutating_policy(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_lifecycle import (
        human_baseline_report,
    )

    snapshot = material.exam_snapshot()
    observations = {
        "artifact_type": "repository-understanding-human-baseline-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "reviewers": [
            {
                "reviewer_id": "expert-a",
                "expertise": "maintainer",
                "questions": [
                    {
                        "id": "Q01",
                        "outcome": "SUCCESS",
                        "reference_assessment": "AGREE",
                        "time_to_evidence_seconds": 30.0,
                    }
                ],
            },
            {
                "reviewer_id": "expert-b",
                "expertise": "architecture-reviewer",
                "questions": [
                    {
                        "id": "Q01",
                        "outcome": "PARTIAL",
                        "reference_assessment": "SUSPECT",
                        "time_to_evidence_seconds": 50.0,
                    }
                ],
            },
        ],
    }
    path = tmp_path / "human-baseline.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    report = human_baseline_report(
        path,
        question_ids=_question_ids(),
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
    )
    q1 = report["questions"]["Q01"]
    assert report["maturity"] == "MULTI_REVIEWER"
    assert q1["reviewer_count"] == 2
    assert q1["median_time_to_evidence_seconds"] == 40.0
    assert q1["success_rate_percent"] == 50.0
    assert q1["reference_disagreement"] is True
    assert report["reference_disagreement_questions"] == ["Q01"]
    assert "cannot automatically change" in report["authority_note"]


def test_active_promotion_requires_empirical_human_and_adjudication_evidence(
    tmp_path: Path,
) -> None:
    import copy
    import pytest
    from oh_goon.repository_tooling.repository_understanding_lifecycle import (
        authorize_question_transition,
    )

    lifecycle = copy.deepcopy(exam_module.question_lifecycle())
    q1 = lifecycle["questions"][0]
    q1.update(
        {
            "state": "CALIBRATING",
            "score_bearing": False,
            "candidate_delivery": True,
            "delivery_mode": "CALIBRATION_NON_SCORE_BEARING",
            "activation_basis": None,
        }
    )
    import hashlib

    lifecycle_body = {
        key: value for key, value in lifecycle.items() if key != "lifecycle_identity"
    }
    lifecycle["lifecycle_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                lifecycle_body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    )
    calibration = {
        "source_version": lifecycle["source_version"],
        "questions": {"Q01": {"observation_count": 4}},
    }
    calibration["report_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                calibration, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )
    human = {
        "source_version": lifecycle["source_version"],
        "questions": {"Q01": {"reviewer_count": 2, "reference_disagreement": True}},
    }
    human["human_baseline_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                human, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )
    request = {
        "artifact_type": "repository-question-lifecycle-transition-request",
        "schema_version": 1,
        "policy_version": lifecycle["policy_version"],
        "source_version": lifecycle["source_version"],
        "lifecycle_identity": lifecycle["lifecycle_identity"],
        "question_id": "Q01",
        "from_state": "CALIBRATING",
        "to_state": "ACTIVE",
        "calibration_report_identity": calibration["report_identity"],
        "human_baseline_identity": human["human_baseline_identity"],
        "promotion_review_identity": "sha256:" + "3" * 64,
    }
    request_path = tmp_path / "transition.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError, match="adjudication_identity"):
        authorize_question_transition(
            request_path,
            lifecycle=lifecycle,
            question_audit=exam_module.question_audit(),
            calibration_report=calibration,
            human_baseline=human,
        )
    request["adjudication_identity"] = "sha256:" + "4" * 64
    request_path.write_text(json.dumps(request), encoding="utf-8")
    result = authorize_question_transition(
        request_path,
        lifecycle=lifecycle,
        question_audit=exam_module.question_audit(),
        calibration_report=calibration,
        human_baseline=human,
    )
    assert result["authorized"] is True
    assert result["next_state_projection"]["score_bearing"] is True
    assert (
        result["evidence"]["adjudication_identity"] == request["adjudication_identity"]
    )


def test_lifecycle_telemetry_never_auto_promotes_question(tmp_path: Path) -> None:
    import copy
    from oh_goon.repository_tooling.repository_understanding_lifecycle import (
        authorize_question_transition,
    )

    lifecycle = copy.deepcopy(exam_module.question_lifecycle())
    q1 = lifecycle["questions"][0]
    q1.update(
        {
            "state": "CALIBRATING",
            "score_bearing": False,
            "candidate_delivery": True,
            "delivery_mode": "CALIBRATION_NON_SCORE_BEARING",
            "activation_basis": None,
        }
    )
    import hashlib

    lifecycle_body = {
        key: value for key, value in lifecycle.items() if key != "lifecycle_identity"
    }
    lifecycle["lifecycle_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                lifecycle_body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    )
    calibration = {
        "source_version": lifecycle["source_version"],
        "questions": {"Q01": {"observation_count": 999}},
    }
    calibration["report_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                calibration, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )
    human = {
        "source_version": lifecycle["source_version"],
        "questions": {"Q01": {"reviewer_count": 99, "reference_disagreement": False}},
    }
    human["human_baseline_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                human, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )
    request = {
        "artifact_type": "repository-question-lifecycle-transition-request",
        "schema_version": 1,
        "policy_version": lifecycle["policy_version"],
        "source_version": lifecycle["source_version"],
        "lifecycle_identity": lifecycle["lifecycle_identity"],
        "question_id": "Q01",
        "from_state": "CALIBRATING",
        "to_state": "ACTIVE",
        "calibration_report_identity": calibration["report_identity"],
        "human_baseline_identity": human["human_baseline_identity"],
        "promotion_review_identity": "not-an-identity",
    }
    path = tmp_path / "transition-no-review.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="promotion_review_identity"):
        authorize_question_transition(
            path,
            lifecycle=lifecycle,
            question_audit=exam_module.question_audit(),
            calibration_report=calibration,
            human_baseline=human,
        )


def test_canary_and_fresh_shadow_pools_rotate_without_exposing_roles(
    tmp_path: Path,
) -> None:
    canaries: set[str] = set()
    fresh: set[str] = set()
    for index in range(80):
        challenge, _ = _challenge_instance(tmp_path, f"rotation-{index}")
        canaries.update(
            row["challenge_id"]
            for row in challenge["secret_bindings"]
            if row["role"] == "CANARY"
        )
        fresh.update(
            row["challenge_id"]
            for row in challenge["secret_bindings"]
            if row["role"] == "FRESH_SHADOW"
        )
        rendered = json.dumps(challenge["public_delivery"])
        assert "CANARY" not in rendered
        assert "FRESH_SHADOW" not in rendered
    assert canaries == {"CANARY-001", "CANARY-002", "CANARY-003"}
    assert fresh == {"FRESH-001", "FRESH-002"}


def test_rotating_hidden_selection_rejects_seed_swap_after_self_hash_recomputed(
    tmp_path: Path,
) -> None:
    import hashlib
    import pytest
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        benchmark_integrity_report,
        challenge_set,
    )

    original, challenge_path = _challenge_instance(tmp_path, "rotation-original")
    original_hidden = {
        row["challenge_id"]
        for row in original["secret_bindings"]
        if row["role"] in {"CANARY", "FRESH_SHADOW"}
    }
    snapshot = material.exam_snapshot()
    replacement_seed = None
    for index in range(1, 100):
        seed = f"rotation-tamper-{index}"
        candidate = challenge_set(
            exam_module.benchmark_registry(),
            questions=material.load_exam(),
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
            seed=seed,
        )
        hidden = {
            row["challenge_id"]
            for row in candidate["secret_bindings"]
            if row["role"] in {"CANARY", "FRESH_SHADOW"}
        }
        if hidden != original_hidden:
            replacement_seed = seed
            break
    assert replacement_seed is not None

    tampered = json.loads(challenge_path.read_text(encoding="utf-8"))
    tampered["secret_rotation_seed"] = replacement_seed
    tampered["seed_identity"] = (
        "sha256:" + hashlib.sha256(replacement_seed.encode("utf-8")).hexdigest()
    )
    body = {
        key: value
        for key, value in tampered.items()
        if key != "challenge_instance_identity"
    }
    tampered["challenge_instance_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )
    challenge_path.write_text(json.dumps(tampered), encoding="utf-8")

    evidence = _benchmark_execution_evidence(original)
    evidence["challenge_instance_identity"] = tampered["challenge_instance_identity"]
    evidence_path = tmp_path / "rotation-seed-tamper-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="rotating selection"):
        benchmark_integrity_report(
            evidence_path,
            registry=exam_module.benchmark_registry(),
            source_version=original["source_version"],
            repository_identity=original["repository_identity"],
            exam_snapshot_sha256=original["exam_snapshot_sha256"],
            challenge_instance_path=challenge_path,
        )


def _capability_run_evidence(
    tmp_path: Path,
    result_path: Path,
    *,
    index: int,
    comparable: bool = True,
    adversarial_clean: bool = True,
) -> tuple[Path, Path]:
    from oh_goon.repository_tooling.repository_understanding_integrity import (
        BENCHMARK_CONTROL_REQUIREMENTS,
        adversarial_report,
        benchmark_integrity_report,
        challenge_set,
    )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    snapshot = material.exam_snapshot()
    challenge = challenge_set(
        exam_module.benchmark_registry(),
        questions=material.load_exam(),
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
        seed=f"capability-seed-{index}",
    )
    challenge_path = tmp_path / f"capability-challenge-{index}.json"
    challenge_path.write_text(json.dumps(challenge), encoding="utf-8")

    observations = _adversarial_observations(challenge)
    if not adversarial_clean:
        target_binding = next(
            binding
            for binding in challenge["secret_bindings"]
            if binding["critical"] and binding["role"] == "CONTRADICTION_PRESSURE"
        )
        target = next(
            row
            for row in observations["challenges"]
            if row["delivery_id"] == target_binding["delivery_id"]
        )
        target["outcome"] = "UNSUPPORTED_YIELD"
    observations_path = tmp_path / f"capability-adversarial-observations-{index}.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    adversarial = adversarial_report(
        challenge_path,
        observations_path,
        registry=exam_module.benchmark_registry(),
    )
    adversarial_path = tmp_path / f"capability-adversarial-result-{index}.json"
    adversarial_path.write_text(json.dumps(adversarial), encoding="utf-8")

    controls = dict(BENCHMARK_CONTROL_REQUIREMENTS)
    if not comparable:
        controls["network_access"] = "ALLOWED"
    evidence = {
        "artifact_type": "repository-understanding-benchmark-execution-evidence",
        "schema_version": 1,
        "source_version": result["source_version"],
        "repository_identity": result["repository_identity"],
        "exam_snapshot_sha256": result["exam_snapshot_sha256"],
        "benchmark_registry_identity": challenge["benchmark_registry_identity"],
        "challenge_instance_identity": challenge["challenge_instance_identity"],
        "candidate_run_identity": result["candidate_run_identity"],
        "candidate_configuration_identity": result["candidate_configuration_identity"],
        "controls": controls,
    }
    evidence_path = tmp_path / f"capability-integrity-evidence-{index}.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    integrity = benchmark_integrity_report(
        evidence_path,
        registry=exam_module.benchmark_registry(),
        source_version=result["source_version"],
        repository_identity=result["repository_identity"],
        exam_snapshot_sha256=result["exam_snapshot_sha256"],
        challenge_instance_path=challenge_path,
    )
    integrity_path = tmp_path / f"capability-integrity-result-{index}.json"
    integrity_path.write_text(json.dumps(integrity), encoding="utf-8")
    return integrity_path, adversarial_path


def _capability_fixture(
    tmp_path: Path, *, profile: str = "change-author.v1"
) -> tuple[Path, list[Path], list[Path], list[Path]]:
    config = _candidate_configuration(tmp_path, f"capability-config-{profile}.json")
    results = [
        _stable_result(tmp_path, index, config, profile=profile) for index in (1, 2, 3)
    ]
    integrity = []
    adversarial = []
    for index, result in enumerate(results, start=1):
        integrity_path, adversarial_path = _capability_run_evidence(
            tmp_path, result, index=index
        )
        integrity.append(integrity_path)
        adversarial.append(adversarial_path)
    return config, results, integrity, adversarial


def test_capability_certificate_derives_standard_scope_from_change_author_profile(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    _config, results, integrity, adversarial = _capability_fixture(tmp_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T12:00:00Z",
    )
    assert certificate["certificate_status"] == "AUTHORIZED"
    assert certificate["target_tier"] == "STANDARD"
    assert certificate["granted_tier"] == "STANDARD"
    assert certificate["authorization"] == {
        "planning": True,
        "code_review": True,
        "standard_editing": True,
        "critical_execution": False,
        "release_promotion": False,
    }
    assert certificate["passes"] == 3
    assert certificate["comparable_runs"] == 3
    assert certificate["adversarial_qualified_runs"] == 3
    assert len(set(certificate["challenge_instance_identities"])) == 3
    assert certificate["gate_failures"] == []
    assert certificate["freshness"]["time_expiry"] is None
    assert certificate["freshness"]["repository_identity_must_match"] is True
    assert certificate["enforcement_status"] == "EVIDENCE_ONLY"
    assert certificate["capability_certificate_identity"].startswith("sha256:")


def test_capability_tier_is_derived_from_profile_not_candidate_request(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    for profile, expected in (
        ("read-only.v1", "PLAN"),
        ("release-author.v1", "CRITICAL"),
    ):
        sub = tmp_path / expected.lower()
        sub.mkdir()
        _config, results, integrity, adversarial = _capability_fixture(
            sub, profile=profile
        )
        certificate = capability_certificate(
            results,
            integrity_paths=integrity,
            adversarial_paths=adversarial,
            issued_at_utc="2026-09-05T12:00:00Z",
        )
        assert certificate["target_tier"] == expected
        assert certificate["granted_tier"] == expected
    assert certificate["authorization"]["critical_execution"] is True
    assert certificate["authorization"]["release_promotion"] is True


def test_capability_certificate_denies_noncomparable_run_without_weakening_stability(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    config = _candidate_configuration(tmp_path)
    results = [_stable_result(tmp_path, index, config) for index in (1, 2, 3)]
    integrity = []
    adversarial = []
    for index, result in enumerate(results, start=1):
        integrity_path, adversarial_path = _capability_run_evidence(
            tmp_path,
            result,
            index=index,
            comparable=index != 2,
        )
        integrity.append(integrity_path)
        adversarial.append(adversarial_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T12:00:00Z",
    )
    assert certificate["passes"] == 3
    assert certificate["certificate_status"] == "DENIED"
    assert "benchmark_not_comparable:candidate-run-2" in certificate["gate_failures"]
    assert not any(certificate["authorization"].values())


def test_capability_certificate_denies_critical_adversarial_failure(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    config = _candidate_configuration(tmp_path)
    results = [_stable_result(tmp_path, index, config) for index in (1, 2, 3)]
    integrity = []
    adversarial = []
    for index, result in enumerate(results, start=1):
        integrity_path, adversarial_path = _capability_run_evidence(
            tmp_path,
            result,
            index=index,
            adversarial_clean=index != 3,
        )
        integrity.append(integrity_path)
        adversarial.append(adversarial_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T12:00:00Z",
    )
    assert certificate["certificate_status"] == "DENIED"
    assert (
        "critical_adversarial_failure:candidate-run-3" in certificate["gate_failures"]
    )
    assert certificate["adversarial_qualified_runs"] == 2


def test_capability_certificate_rejects_integrity_run_identity_substitution(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    _config, results, integrity, adversarial = _capability_fixture(tmp_path)
    payload = json.loads(integrity[2].read_text(encoding="utf-8"))
    payload["candidate_run_identity"] = "candidate-run-2"
    payload["integrity_result_identity"] = "sha256:" + material._canonical_json_sha256(
        {
            key: value
            for key, value in payload.items()
            if key != "integrity_result_identity"
        }
    )
    integrity[2].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        ValueError, match="one unique integrity result per candidate run"
    ):
        capability_certificate(
            results,
            integrity_paths=integrity,
            adversarial_paths=adversarial,
            issued_at_utc="2026-09-05T12:00:00Z",
        )


def test_capability_certificate_rejects_reused_hidden_challenge_instance(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    _config, results, integrity, adversarial = _capability_fixture(tmp_path)
    second = json.loads(integrity[1].read_text(encoding="utf-8"))
    first = json.loads(integrity[0].read_text(encoding="utf-8"))
    second["challenge_instance_identity"] = first["challenge_instance_identity"]
    second["integrity_result_identity"] = "sha256:" + material._canonical_json_sha256(
        {
            key: value
            for key, value in second.items()
            if key != "integrity_result_identity"
        }
    )
    integrity[1].write_text(json.dumps(second), encoding="utf-8")
    with pytest.raises(ValueError, match="distinct hidden challenge instances"):
        capability_certificate(
            results,
            integrity_paths=integrity,
            adversarial_paths=adversarial,
            issued_at_utc="2026-09-05T12:00:00Z",
        )


def test_capability_check_binds_exact_repository_candidate_and_requested_action(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
        capability_check,
    )

    _config, results, integrity, adversarial = _capability_fixture(tmp_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T12:00:00Z",
    )
    path = tmp_path / "capability-certificate.json"
    path.write_text(json.dumps(certificate), encoding="utf-8")
    allowed = capability_check(
        path,
        repository_identity=certificate["repository_identity"],
        candidate_configuration_identity=certificate[
            "candidate_configuration_identity"
        ],
        requested_capability="standard_editing",
    )
    assert allowed["authorized"] is True
    denied = capability_check(
        path,
        repository_identity=certificate["repository_identity"],
        candidate_configuration_identity=certificate[
            "candidate_configuration_identity"
        ],
        requested_capability="critical_execution",
    )
    assert denied["authorized"] is False
    assert denied["failures"] == ["requested_capability_not_granted"]
    wrong_repo = capability_check(
        path,
        repository_identity="sha256:" + "9" * 64,
        candidate_configuration_identity=certificate[
            "candidate_configuration_identity"
        ],
        requested_capability="planning",
    )
    assert wrong_repo["failures"] == ["repository_identity_mismatch"]
    wrong_candidate = capability_check(
        path,
        repository_identity=certificate["repository_identity"],
        candidate_configuration_identity="sha256:" + "8" * 64,
        requested_capability="planning",
    )
    assert wrong_candidate["failures"] == ["candidate_configuration_identity_mismatch"]


def test_capability_check_rejects_tampered_certificate_even_if_grant_looks_stronger(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
        capability_check,
    )

    _config, results, integrity, adversarial = _capability_fixture(tmp_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T12:00:00Z",
    )
    certificate["authorization"]["critical_execution"] = True
    path = tmp_path / "tampered-capability.json"
    path.write_text(json.dumps(certificate), encoding="utf-8")
    with pytest.raises(ValueError, match="identity does not match artifact bytes"):
        capability_check(
            path,
            repository_identity=certificate["repository_identity"],
            candidate_configuration_identity=certificate[
                "candidate_configuration_identity"
            ],
            requested_capability="critical_execution",
        )


def test_capability_certificate_denies_critical_concept_instability_even_when_three_runs_qualify(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    config = _candidate_configuration(tmp_path)
    results = [
        _stable_result(tmp_path, 1, config),
        _stable_result(tmp_path, 2, config, partial_critical=True),
        _stable_result(tmp_path, 3, config),
    ]
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["qualified"] for path in results
    )
    integrity = []
    adversarial = []
    for index, result in enumerate(results, start=1):
        integrity_path, adversarial_path = _capability_run_evidence(
            tmp_path, result, index=index
        )
        integrity.append(integrity_path)
        adversarial.append(adversarial_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T12:00:00Z",
    )
    assert certificate["certificate_status"] == "DENIED"
    assert "stable_qualification_required" in certificate["gate_failures"]
    assert certificate["critical_concept_oscillations"]
    assert not any(certificate["authorization"].values())


def _recertification_fixture(tmp_path: Path) -> tuple[dict[str, Any], Path, list[Path]]:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    _config, results, integrity, adversarial = _capability_fixture(tmp_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T12:00:00Z",
    )
    certificate_path = tmp_path / "recertification-capability.json"
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    return certificate, certificate_path, results


def _resign_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    snapshot["snapshot_sha256"] = material._canonical_json_sha256(body)
    return snapshot


def test_differential_recertification_same_source_requires_nothing(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
        validate_recertification_plan,
    )

    _certificate, certificate_path, results = _recertification_fixture(tmp_path)
    plan = differential_recertification_plan(
        certificate_path,
        results[0],
        current_snapshot=material.exam_snapshot(),
    )
    validate_recertification_plan(plan)
    assert plan["status"] == "NO_RECERTIFICATION_REQUIRED"
    assert plan["source_changed"] is False
    assert plan["recertification_concepts"] == []
    assert plan["recertification_question_ids"] == []
    assert plan["capability_state"] == "AUTHORIZED"
    assert plan["enforcement_status"] == "EVIDENCE_ONLY"


def test_differential_recertification_source_change_suspends_certificate_even_when_declared_bindings_match(
    tmp_path: Path,
) -> None:
    from copy import deepcopy
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
    )

    _certificate, certificate_path, results = _recertification_fixture(tmp_path)
    current = deepcopy(material.exam_snapshot())
    current["source_version"] = "1267.0.999"
    current["repository_identity"] = "sha256:" + "7" * 64
    _resign_snapshot(current)
    plan = differential_recertification_plan(
        certificate_path, results[0], current_snapshot=current
    )
    assert plan["status"] == "SOURCE_CHANGED_DECLARED_CONCEPT_BINDINGS_UNCHANGED"
    assert plan["capability_state"] == "SUSPENDED_SOURCE_CHANGED"
    assert plan["reissue_required"] is True
    assert plan["recertification_question_ids"] == []
    assert len(plan["preserved_concepts"]) == 11


def test_differential_recertification_invalidates_only_concept_whose_declared_evidence_range_changed(
    tmp_path: Path,
) -> None:
    from copy import deepcopy
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
    )

    _certificate, certificate_path, results = _recertification_fixture(tmp_path)
    current = deepcopy(material.exam_snapshot())
    target = current["questions"][0]
    concept = target["public"]["blueprint"]["concept_family"]
    target["secret"]["evidence_reference_identities"][0]["range_sha256"] = (
        "sha256:" + "a" * 64
    )
    current["source_version"] = "1267.0.999"
    current["repository_identity"] = "sha256:" + "6" * 64
    _resign_snapshot(current)
    plan = differential_recertification_plan(
        certificate_path, results[0], current_snapshot=current
    )
    expected_questions = [
        row["id"]
        for row in current["questions"]
        if row["public"]["blueprint"]["concept_family"] == concept
        and row["secret"]["question_lifecycle"]["state"] == "ACTIVE"
    ]
    assert plan["status"] == "DIFFERENTIAL_RECERTIFICATION_REQUIRED"
    assert plan["affected_concepts"] == [concept]
    assert plan["recertification_concepts"] == [concept]
    assert plan["recertification_question_ids"] == expected_questions
    assert concept not in plan["preserved_concepts"]
    assert len(plan["preserved_concepts"]) == 10


def test_differential_recertification_semantic_claim_change_invalidates_owning_concept(
    tmp_path: Path,
) -> None:
    from copy import deepcopy
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
    )

    _certificate, certificate_path, results = _recertification_fixture(tmp_path)
    current = deepcopy(material.exam_snapshot())
    target = current["questions"][5]
    concept = target["public"]["blueprint"]["concept_family"]
    target["secret"]["expected_claims"][0]["statement"] += " changed"
    current["repository_identity"] = "sha256:" + "5" * 64
    _resign_snapshot(current)
    plan = differential_recertification_plan(
        certificate_path, results[0], current_snapshot=current
    )
    assert plan["affected_concepts"] == [concept]
    assert target["id"] in plan["recertification_question_ids"]


def test_differential_recertification_unmapped_source_change_is_diagnostic_not_silent_authority(
    tmp_path: Path,
) -> None:
    from copy import deepcopy
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
    )

    _certificate, certificate_path, results = _recertification_fixture(tmp_path)
    current = deepcopy(material.exam_snapshot())
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        _declared_evidence_paths,
    )

    unmapped = sorted(
        set(current["repository_evidence_index"]) - _declared_evidence_paths(current)
    )
    assert unmapped
    changed_path = unmapped[0]
    current["repository_evidence_index"][changed_path]["sha256"] = "1" * 64
    current["repository_identity"] = "sha256:" + "4" * 64
    _resign_snapshot(current)
    plan = differential_recertification_plan(
        certificate_path, results[0], current_snapshot=current
    )
    assert plan["status"] == "SOURCE_CHANGED_DECLARED_CONCEPT_BINDINGS_UNCHANGED"
    assert changed_path in plan["unmapped_changed_paths"]
    assert plan["capability_state"] == "SUSPENDED_SOURCE_CHANGED"
    assert plan["enforcement_status"] == "EVIDENCE_ONLY"


def test_differential_recertification_rejects_prior_result_not_bound_to_certificate(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
    )

    _certificate, certificate_path, results = _recertification_fixture(tmp_path)
    payload = json.loads(results[0].read_text(encoding="utf-8"))
    payload["candidate_run_identity"] = "substituted-run"
    payload["regrade_bundle"]["candidate_run_identity"] = "substituted-run"
    payload["certificate_identity"] = "sha256:" + material._canonical_json_sha256(
        {
            key: value
            for key, value in payload.items()
            if key not in {"certificate_identity", "regrade_bundle", "regraded"}
        }
    )
    substituted = tmp_path / "substituted-result.json"
    substituted.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        ValueError, match="not one of the capability certificate qualifying runs"
    ):
        differential_recertification_plan(
            certificate_path,
            substituted,
            current_snapshot=material.exam_snapshot(),
        )


def test_differential_recertification_rejects_tampered_certificate(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
    )

    certificate, certificate_path, results = _recertification_fixture(tmp_path)
    certificate["granted_tier"] = "CRITICAL"
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    with pytest.raises(ValueError, match="identity does not match artifact bytes"):
        differential_recertification_plan(
            certificate_path,
            results[0],
            current_snapshot=material.exam_snapshot(),
        )


def _game_tape_fixture(
    tmp_path: Path,
) -> tuple[list[Path], list[Path], list[Path], dict[str, Any], Path]:
    from oh_goon.repository_tooling.repository_understanding_capability import (
        capability_certificate,
    )

    _config, results, integrity, adversarial = _capability_fixture(tmp_path)
    certificate = capability_certificate(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        issued_at_utc="2026-09-05T14:00:00Z",
    )
    certificate_path = tmp_path / "game-tape-capability.json"
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    return results, integrity, adversarial, certificate, certificate_path


def test_game_tape_projects_complete_certificate_explainability_without_new_authority(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_game_tape import (
        GAME_TAPE_TABS,
        game_tape,
        validate_game_tape,
    )

    results, integrity, adversarial, certificate, certificate_path = _game_tape_fixture(
        tmp_path
    )
    tape = game_tape(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        capability_path=certificate_path,
    )
    validate_game_tape(tape)
    assert tape["tab_order"] == list(GAME_TAPE_TABS)
    assert list(tape["tabs"]) == list(GAME_TAPE_TABS)
    assert tape["authority_contract"]["projection_only"] is True
    assert tape["authority_contract"]["may_change_score"] is False
    assert tape["authority_contract"]["may_grant_capability"] is False
    assert tape["authority_contract"]["may_reauthorize_stale_certificate"] is False
    assert tape["tabs"]["overview"]["certificate_status"] == "AUTHORIZED"
    assert tape["tabs"]["overview"]["granted_tier"] == "STANDARD"
    assert tape["tabs"]["overview"]["runs"] == {"qualified": 3, "required": 3}
    assert len(tape["tabs"]["concepts"]) == len(certificate["certified_concepts"])
    assert len(tape["tabs"]["questions"]) == 157
    assert len(tape["tabs"]["benchmark_integrity"]) == 3
    assert all(row["comparable"] for row in tape["tabs"]["benchmark_integrity"])
    assert all(
        row["adversarial_qualified"] for row in tape["tabs"]["benchmark_integrity"]
    )
    assert (
        tape["tabs"]["qualification_capability"]["authorization"]
        == certificate["authorization"]
    )
    assert tape["tabs"]["recertification"] is None
    assert tape["game_tape_identity"].startswith("sha256:")
    rendered = json.dumps(tape)
    assert "expected_claims" not in rendered
    assert "evidence_expectations" not in rendered
    assert "why_it_matters" not in rendered


def test_game_tape_rejects_validly_rehashed_but_unreproducible_capability_projection(
    tmp_path: Path,
) -> None:
    import hashlib
    import pytest
    from oh_goon.repository_tooling.repository_understanding_game_tape import game_tape

    results, integrity, adversarial, certificate, certificate_path = _game_tape_fixture(
        tmp_path
    )
    certificate["median_composite_understanding"] = (
        float(certificate["median_composite_understanding"]) - 0.1
    )
    body = {
        key: value
        for key, value in certificate.items()
        if key != "capability_certificate_identity"
    }
    certificate["capability_certificate_identity"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be reproduced"):
        game_tape(
            results,
            integrity_paths=integrity,
            adversarial_paths=adversarial,
            capability_path=certificate_path,
        )


def test_game_tape_binds_differential_recertification_and_surfaces_what_remains(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_game_tape import game_tape
    from oh_goon.repository_tooling.repository_understanding_recertification import (
        differential_recertification_plan,
    )

    results, integrity, adversarial, certificate, certificate_path = _game_tape_fixture(
        tmp_path
    )
    plan = differential_recertification_plan(
        certificate_path,
        results[0],
        current_snapshot=material.exam_snapshot(),
    )
    plan_path = tmp_path / "game-tape-recertification.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    tape = game_tape(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        capability_path=certificate_path,
        recertification_path=plan_path,
    )
    assert tape["tabs"]["recertification"]["status"] == "NO_RECERTIFICATION_REQUIRED"
    assert tape["tabs"]["overview"]["what_remains"]["recertification_concepts"] == []
    assert (
        tape["tabs"]["overview"]["what_remains"]["recertification_question_ids"] == []
    )
    assert (
        tape["input_identities"]["recertification_plan_identity"]
        == plan["recertification_plan_identity"]
    )


def test_game_tape_human_report_leads_with_authority_and_what_remains(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_game_tape import (
        game_tape,
        human_game_tape_report,
    )

    results, integrity, adversarial, _certificate, certificate_path = (
        _game_tape_fixture(tmp_path)
    )
    tape = game_tape(
        results,
        integrity_paths=integrity,
        adversarial_paths=adversarial,
        capability_path=certificate_path,
    )
    report = human_game_tape_report(tape)
    assert report.startswith("OH-GOON QA GAME TAPE\n")
    assert "What remains" in report
    assert "Capability tier        STANDARD" in report
    assert "Projection only" in report
    assert "Game Tape identity" in report


def test_calibration_observation_provenance_identity_and_duplicate_run_rejection(
    tmp_path: Path,
) -> None:
    import copy
    import pytest
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    run = {
        "system_id": "system-a",
        "candidate_identity": "sha256:" + "1" * 64,
        "model_identity": "sha256:" + "2" * 64,
        "grading_artifact_identity": "sha256:" + "3" * 64,
        "qualification_profile": "release-author",
        "cohort": "STRONG",
        "questions": [{"id": "Q01", "success": True}],
    }
    payload = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "runs": [run],
    }
    path = tmp_path / "provenance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = calibration_report(
        path,
        question_ids=authored,
        authored_difficulty=authored,
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
    )
    q1 = report["questions"]["Q01"]
    assert report["unique_run_count"] == 1
    assert q1["unique_run_count"] == 1
    assert q1["provenance_complete"] is True
    assert len(q1["observation_identities"]) == 1
    assert q1["observation_identities"][0].startswith("sha256:")

    duplicate = copy.deepcopy(payload)
    duplicate["runs"].append(copy.deepcopy(run))
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate calibration run identity"):
        calibration_report(
            path,
            question_ids=authored,
            authored_difficulty=authored,
            source_version=snapshot["source_version"],
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
        )


def test_calibration_review_requires_complete_observation_provenance(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    runs = []
    for cohort in ("STRONG", "MID", "WEAK"):
        for index in range(2):
            runs.append(
                {
                    "system_id": f"{cohort}-{index}",
                    "cohort": cohort,
                    "questions": [{"id": "Q01", "success": cohort != "WEAK"}],
                }
            )
    payload = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "runs": runs,
    }
    path = tmp_path / "incomplete-provenance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = calibration_report(
        path,
        question_ids=authored,
        authored_difficulty=authored,
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
    )
    q1 = report["questions"]["Q01"]
    assert q1["observation_count"] == 6
    assert q1["system_count"] == 6
    assert q1["provenance_complete"] is False
    assert q1["review_eligibility"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert "observation_provenance_incomplete" in q1["review_eligibility"]["gaps"]


def test_same_grading_artifact_cannot_count_twice_for_one_question(
    tmp_path: Path,
) -> None:
    import pytest
    from oh_goon.repository_tooling.repository_understanding_calibration import (
        calibration_report,
    )

    snapshot = material.exam_snapshot()
    authored = {
        question["id"]: question["public"]["difficulty"]
        for question in snapshot["questions"]
    }
    common_grade = "sha256:" + "4" * 64
    runs = []
    for index in range(2):
        runs.append(
            {
                "system_id": f"system-{index}",
                "candidate_identity": "sha256:" + str(index + 5) * 64,
                "grading_artifact_identity": common_grade,
                "qualification_profile": "change-author",
                "cohort": "MID",
                "questions": [{"id": "Q01", "success": True}],
            }
        )
    payload = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "runs": runs,
    }
    path = tmp_path / "duplicate-grade.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        ValueError, match="grading artifact is represented by more than one"
    ):
        calibration_report(
            path,
            question_ids=authored,
            authored_difficulty=authored,
            source_version=snapshot["source_version"],
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
        )


def test_shadow_external_answerability_fails_closed_on_secret_evidence_reference() -> (
    None
):
    from dataclasses import replace
    import pytest
    from oh_goon.repository_tooling.repository_understanding_material import (
        _assert_external_answerability_ready,
    )

    questions = list(material.load_exam())
    lifecycle = exam_module.question_lifecycle()
    shadow_id = next(
        row["id"]
        for row in lifecycle["questions"]
        if not row["qualification_bank_member"]
    )
    index = next(i for i, question in enumerate(questions) if question.id == shadow_id)
    questions[index] = replace(
        questions[index],
        evidence_expectations=(
            "docs/certification/answers/01-repository-ownership.yml:1-2",
        ),
    )
    with pytest.raises(ValueError, match="shadow answerability is not release-ready"):
        _assert_external_answerability_ready(tuple(questions), lifecycle)


def test_shadow_question_health_maintenance_is_structural_and_non_mutating() -> None:
    manifest = exam_module.verify()
    report = manifest["question_health_maintenance"]
    assert (
        report["policy_version"]
        == "repository-understanding-question-health-maintenance.v1"
    )
    assert report["scope"] == "PRE_PROMOTION_SHADOW_POOL"
    assert report["question_count"] == 63
    assert report["structurally_ready_count"] == 63
    assert report["structural_blocker_count"] == 0
    assert report["status"] == "READY"
    assert report["health_state_authority"] == "SECRET_QUESTION_AUDIT"
    assert report["automatic_health_mutation"] is False
    assert report["automatic_lifecycle_transition"] is False
    assert set(report["empirical_states_requiring_observations"]) == {
        "NON_DISCRIMINATING"
    }
    assert all(
        row["semantic_health_review"] == "REQUIRED" for row in report["questions"]
    )
    assert all(
        row["empirical_discrimination_review"] == "REQUIRED"
        for row in report["questions"]
    )


def test_shadow_health_maintenance_blocks_duplicate_like_prompt_without_mutating_health() -> (
    None
):
    from dataclasses import replace
    from oh_goon.repository_tooling.repository_understanding_material import (
        _assert_external_answerability_ready,
        _assert_question_health_maintenance_ready,
        _question_health_maintenance_audit,
    )

    questions = list(material.load_exam())
    lifecycle = exam_module.question_lifecycle()
    audit = exam_module.question_audit()
    shadow_ids = [
        row["id"]
        for row in lifecycle["questions"]
        if not row["qualification_bank_member"]
    ]
    first_id, second_id = shadow_ids[:2]
    first = next(question for question in questions if question.id == first_id)
    second_index = next(
        index for index, question in enumerate(questions) if question.id == second_id
    )
    questions[second_index] = replace(questions[second_index], prompt=first.prompt)
    answerability = _assert_external_answerability_ready(tuple(questions), lifecycle)
    report = _question_health_maintenance_audit(
        tuple(questions), lifecycle, audit, answerability
    )
    assert report["status"] == "BLOCKED"
    assert report["structural_blocker_count"] >= 2
    by_id = {row["id"]: row for row in report["questions"]}
    assert "POTENTIAL_DUPLICATE" in by_id[first_id]["structural_signals"]
    assert second_id in by_id[first_id]["duplicate_candidate_ids"]
    assert {row["health_status"] for row in audit["questions"]} == {"VALID"}
    with pytest.raises(
        ValueError, match="shadow question health is not promotion-ready"
    ):
        _assert_question_health_maintenance_ready(
            tuple(questions), lifecycle, audit, answerability
        )


def test_shadow_questions_never_enter_score_grader_template_before_promotion(
    tmp_path: Path,
) -> None:
    submission = _submission(tmp_path)
    sheet = exam_module.grade_template(submission=submission)
    graded_ids = [row["id"] for row in sheet["grades"]]
    assert graded_ids == [f"Q{index:02d}" for index in range(1, 148)] + [
        f"Q{index}" for index in range(203, 213)
    ]
    assert len(sheet["exam_snapshot"]["questions"]) == 157
    assert sheet["exam_snapshot"]["external_answerability"]["question_count"] == 63
