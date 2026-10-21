from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from oh_goon.repository_tooling import (
    _repository_understanding_exam_validation as validation,
)
from oh_goon.repository_tooling import (
    repository_understanding_calibration as calibration,
)
from oh_goon.repository_tooling import repository_understanding_lifecycle as lifecycle
from oh_goon.repository_tooling import repository_understanding_material as material

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
DIGEST_A = "a" * 64


def _snapshot() -> dict[str, Any]:
    return material.exam_snapshot()


def _observation_payload() -> dict[str, Any]:
    # Deliberately mutable JSON: negative cases replace nested members with invalid shapes.
    snapshot = _snapshot()
    return {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": 1,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "runs": [
            {
                "system_id": "system-a",
                "candidate_identity": SHA_A,
                "grading_artifact_identity": SHA_B,
                "qualification_profile": "change-author",
                "model_identity": SHA_A,
                "configuration_identity": SHA_B,
                "system_family": "family-a",
                "provider": "provider-a",
                "cohort": "STRONG",
                "questions": [
                    {
                        "id": "Q01",
                        "success": True,
                        "tool_calls": 2,
                        "time_to_evidence_seconds": 1.5,
                        "context_tokens": 100,
                        "failure_mode": None,
                    }
                ],
            }
        ],
    }


def _report(tmp_path: Path, payload: dict[str, Any] | None = None) -> dict[str, object]:
    snapshot = _snapshot()
    authored = {row["id"]: row["public"]["difficulty"] for row in snapshot["questions"]}
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(payload or _observation_payload()), encoding="utf-8")
    return calibration.calibration_report(
        path,
        question_ids=authored,
        authored_difficulty=authored,
        source_version=str(snapshot["source_version"]),
        repository_identity=str(snapshot["repository_identity"]),
        exam_snapshot_sha256=str(snapshot["snapshot_sha256"]),
    )


@pytest.mark.parametrize(
    ("value", "label"),
    [(None, "x"), ("", "x"), ("sha256:abc", "x"), ("sha256:" + "g" * 64, "x")],
)
def test_identity_validation_rejects_malformed_authority(
    value: object, label: str
) -> None:
    with pytest.raises(ValueError, match="sha256 identity"):
        calibration._identity(value, label=label)


@pytest.mark.parametrize("value", [None, "", "a" * 63, "g" * 64])
def test_digest_validation_rejects_malformed_digest(value: object) -> None:
    with pytest.raises(ValueError, match="sha256 digest"):
        calibration._sha256_digest(value, label="digest")


def test_wilson_and_review_eligibility_cover_empty_invalid_and_ready_states() -> None:
    assert calibration._wilson_interval(0, 0) is None
    with pytest.raises(ValueError, match="between 0 and total"):
        calibration._wilson_interval(2, 1)
    insufficient = calibration._calibration_review_eligibility(
        observation_count=1,
        system_count=1,
        cohort_system_counts={"STRONG": 1},
        independent_subject_count=1,
        provenance_complete=False,
    )
    assert insufficient["status"] == "INSUFFICIENT_EVIDENCE"
    assert "observation_provenance_incomplete" in insufficient["gaps"]
    ready = calibration._calibration_review_eligibility(
        observation_count=6,
        system_count=6,
        cohort_system_counts={"STRONG": 2, "MID": 2, "WEAK": 2},
        independent_subject_count=6,
        cohort_independent_subject_counts={"STRONG": 2, "MID": 2, "WEAK": 2},
    )
    assert ready["status"] == "ELIGIBLE_FOR_REVIEW"


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"question_id": ""}, "question_id"),
        ({"calibrated_difficulty": "L9"}, "calibrated_difficulty"),
        ({"decision": "REJECT"}, "decision"),
        ({"repository_identity": "bad"}, "repository_identity"),
        ({"exam_snapshot_sha256": "bad"}, "exam_snapshot_sha256"),
        ({"reviewer_identity": "bad"}, "reviewer_identity"),
    ],
)
def test_promotion_review_rejects_invalid_binding(
    kwargs: dict[str, object], match: str
) -> None:
    values: dict[str, Any] = {
        "source_version": "1.0",
        "repository_identity": SHA_A,
        "exam_snapshot_sha256": DIGEST_A,
        "question_id": "Q01",
        "calibration_report_identity": SHA_B,
        "calibrated_difficulty": "L3",
        "reviewer_identity": SHA_A,
        "evidence_assessment_identity": SHA_B,
        "decision": "APPROVE",
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=match):
        calibration.calibration_promotion_review(**values)


def test_calibration_report_rejects_wrong_top_level_identity_and_schema(
    tmp_path: Path,
) -> None:
    base = _observation_payload()
    mutations = [
        ("artifact_type", "wrong"),
        ("schema_version", 99),
        ("source_version", "wrong"),
        ("repository_identity", SHA_B),
        ("exam_snapshot_sha256", "b" * 64),
        ("runs", "not-a-list"),
    ]
    for key, value in mutations:
        payload = copy.deepcopy(base)
        payload[key] = value
        with pytest.raises(ValueError):
            _report(tmp_path, payload)


def test_calibration_report_rejects_malformed_run_and_question_rows(
    tmp_path: Path,
) -> None:
    base = _observation_payload()
    mutations: list[Callable[[dict[str, Any]], None]] = [
        lambda p: p["runs"].append("bad"),
        lambda p: p["runs"][0].update(system_id=""),
        lambda p: p["runs"][0].update(cohort="UNKNOWN"),
        lambda p: p["runs"][0].update(questions="bad"),
        lambda p: p["runs"][0]["questions"].append("bad"),
        lambda p: p["runs"][0]["questions"][0].update(id="Q999"),
        lambda p: p["runs"][0]["questions"][0].update(success="yes"),
        lambda p: p["runs"][0]["questions"][0].update(tool_calls=-1),
        lambda p: p["runs"][0]["questions"][0].update(time_to_evidence_seconds=-1),
        lambda p: p["runs"][0]["questions"][0].update(context_tokens=-1),
        lambda p: p["runs"][0]["questions"][0].update(failure_mode=7),
    ]
    for mutate in mutations:
        payload = copy.deepcopy(base)
        mutate(payload)
        with pytest.raises(ValueError):
            _report(tmp_path, payload)


def test_calibration_report_rejects_duplicate_run_identity(tmp_path: Path) -> None:
    payload = _observation_payload()
    payload["runs"].append(copy.deepcopy(payload["runs"][0]))
    with pytest.raises(ValueError, match="duplicate calibration run identity"):
        _report(tmp_path, payload)


def test_calibration_report_rejects_cross_cohort_system_and_subject_reuse(
    tmp_path: Path,
) -> None:
    for mutation, match in (
        ("system", "system_id cannot appear in multiple cohorts"),
        ("subject", "independent subject cannot appear in multiple cohorts"),
    ):
        payload = _observation_payload()
        second = copy.deepcopy(payload["runs"][0])
        second["cohort"] = "WEAK"
        second["questions"][0]["id"] = "Q02"
        if mutation == "system":
            second["candidate_identity"] = SHA_B
        else:
            second["system_id"] = "system-b"
            second["candidate_identity"] = SHA_B
        payload["runs"].append(second)
        with pytest.raises(ValueError, match=match):
            _report(tmp_path, payload)


def test_calibration_report_rejects_duplicate_question_and_grading_binding(
    tmp_path: Path,
) -> None:
    payload = _observation_payload()
    payload["runs"][0]["questions"].append(
        copy.deepcopy(payload["runs"][0]["questions"][0])
    )
    with pytest.raises(ValueError, match="unknown/duplicate question id"):
        _report(tmp_path, payload)

    payload = _observation_payload()
    second = copy.deepcopy(payload["runs"][0])
    second["system_id"] = "system-b"
    second["candidate_identity"] = SHA_B
    second["model_identity"] = SHA_B
    second["configuration_identity"] = SHA_A
    payload["runs"].append(second)
    with pytest.raises(
        ValueError,
        match="grading artifact is represented by more than one calibration observation",
    ):
        _report(tmp_path, payload)


def test_calibration_report_rejects_malformed_optional_provenance(
    tmp_path: Path,
) -> None:
    mutations: list[Callable[[dict[str, Any]], None]] = [
        lambda run: run.update(candidate_identity="bad"),
        lambda run: run.update(grading_artifact_identity="bad"),
        lambda run: run.update(qualification_profile=""),
        lambda run: run.update(model_identity="bad"),
        lambda run: run.update(configuration_identity="bad"),
        lambda run: run.update(system_family=""),
        lambda run: run.update(provider=""),
    ]
    for mutate in mutations:
        payload = _observation_payload()
        mutate(payload["runs"][0])
        with pytest.raises(ValueError):
            _report(tmp_path, payload)


def test_lifecycle_identity_version_and_state_projections_fail_closed() -> None:
    with pytest.raises(ValueError, match="sha256 identity"):
        lifecycle._identity("bad", label="x")
    with pytest.raises(ValueError, match="sha256 identity"):
        lifecycle._assert_embedded_identity({}, field="identity", label="row")
    with pytest.raises(ValueError, match="sha256 identity"):
        lifecycle._assert_embedded_identity(
            {"identity": "bad"}, field="identity", label="row"
        )
    assert (
        lifecycle._version_not_future("1.2.3", label="v", source_version="1.2.3")
        == "1.2.3"
    )
    with pytest.raises(ValueError):
        lifecycle._version_not_future("9999.0.0", label="v", source_version="1.2.3")
    with pytest.raises(ValueError):
        lifecycle._version_not_future(None, label="v", source_version="1.2.3")
    assert (
        lifecycle._version_not_future(
            "PRE_LIFECYCLE", label="v", source_version="1.2.3", allow_legacy=True
        )
        == "PRE_LIFECYCLE"
    )
    assert lifecycle._state_projection("ACTIVE")["score_bearing"] is True
    assert lifecycle._state_projection("QUARANTINED")["candidate_delivery"] is False
    assert lifecycle._state_projection("RETIRED")["delivery_mode"] == "WITHHELD"
    with pytest.raises(KeyError):
        lifecycle._state_projection("UNKNOWN")


def test_material_reference_validation_rejects_absolute_escape_and_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "ok.txt").write_text("line1\nline2\n", encoding="utf-8")
    material._validate_evidence("ok.txt:1-2", root=root)
    identity = material._evidence_reference_identity("ok.txt:1-2", root=root)
    assert identity["reference"] == "ok.txt:1-2"
    for reference in (
        "/etc/passwd:1",
        "../escape.txt:1",
        "missing.txt:1",
        "ok.txt:0",
        "ok.txt:1-99",
        "bad-format",
    ):
        with pytest.raises(ValueError):
            material._validate_evidence(reference, root=root)


def test_material_expected_claims_and_contradictions_reject_bad_shapes() -> None:
    malformed: tuple[object, ...] = (None, "bad", [], [{}])
    for value in malformed:
        with pytest.raises(ValueError):
            material._expected_claims("Q01", value)
    with pytest.raises(ValueError):
        material._contradictions("Q01", "bad", "critical")
    with pytest.raises(ValueError):
        material._contradictions("Q01", [{"id": "c"}], "critical")


@pytest.mark.parametrize("value", [None, "", 1, [], {}])
def test_exam_validated_timestamp_rejects_bad_values(value: object) -> None:
    with pytest.raises(ValueError):
        validation._validated_graded_at(value)


def test_exam_grader_validation_rejects_missing_and_invalid_fields() -> None:
    valid = {
        "type": "llm",
        "identity": "grader-a",
        "rubric_version": "repository-understanding.v17",
        "configuration": {"temperature": 0},
    }
    assert validation._validated_grader(valid)["identity"] == "grader-a"
    for mutation in (
        None,
        {},
        {**valid, "type": ""},
        {**valid, "identity": ""},
        {**valid, "rubric_version": "wrong"},
        {**valid, "configuration": 7},
    ):
        with pytest.raises(ValueError):
            validation._validated_grader(mutation)


def test_exam_snapshot_validation_rejects_wrong_shape_and_digest() -> None:
    snapshot = _snapshot()
    assert (
        validation._validate_snapshot(snapshot)["snapshot_sha256"]
        == snapshot["snapshot_sha256"]
    )
    with pytest.raises(ValueError):
        validation._validate_snapshot("bad")
    with pytest.raises(ValueError):
        validation._validate_snapshot(snapshot, expected_sha256="0" * 64)
    mutated = copy.deepcopy(snapshot)
    mutated["questions"] = []
    with pytest.raises(ValueError):
        validation._validate_snapshot(mutated)
