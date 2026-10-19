"""Prepare, verify, grade, and regrade repository-understanding certification.

Candidate material is PUBLIC plus repository EVIDENCE. SECRET semantic truth is available only
once the candidate submission is frozen. Graders emit structured observations; deterministic
Oh-Goon policy owns scoring and qualification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling._repository_understanding_exam_inputs import (
    _candidate_configuration_bundle,
    _validate_candidate_submission,
)
from oh_goon.repository_tooling._repository_understanding_exam_report import (
    human_report as human_report,
)
from oh_goon.repository_tooling._repository_understanding_exam_validation import _score_inputs
from oh_goon.repository_tooling.repository_understanding_calibration import (
    calibration_report as calibration_report,
)
from oh_goon.repository_tooling.repository_understanding_capability import (
    CAPABILITY_ACTIONS as CAPABILITY_ACTIONS,
)
from oh_goon.repository_tooling.repository_understanding_capability import (
    capability_certificate as capability_certificate,
)
from oh_goon.repository_tooling.repository_understanding_capability import (
    capability_check as capability_check,
)
from oh_goon.repository_tooling.repository_understanding_game_tape import game_tape as game_tape
from oh_goon.repository_tooling.repository_understanding_game_tape import (
    human_game_tape_report as human_game_tape_report,
)
from oh_goon.repository_tooling.repository_understanding_graph import graph_manifest
from oh_goon.repository_tooling.repository_understanding_integrity import (
    adversarial_report as adversarial_report,
)
from oh_goon.repository_tooling.repository_understanding_integrity import (
    benchmark_integrity_report as benchmark_integrity_report,
)
from oh_goon.repository_tooling.repository_understanding_integrity import challenge_set
from oh_goon.repository_tooling.repository_understanding_lifecycle import (
    authorize_question_transition as authorize_question_transition,
)
from oh_goon.repository_tooling.repository_understanding_lifecycle import (
    human_baseline_report as human_baseline_report,
)
from oh_goon.repository_tooling.repository_understanding_material import (
    GRADER_SCHEMA_VERSION,
    QA_SCHEMA_VERSION,
    QUESTION_SCHEMA_VERSION,
    RUBRIC_VERSION,
    _canonical_json_sha256,
    _source_version,
    exam_snapshot,
    load_exam,
    manifest,
)
from oh_goon.repository_tooling.repository_understanding_material import (
    benchmark_registry as benchmark_registry,
)
from oh_goon.repository_tooling.repository_understanding_material import prepare as prepare
from oh_goon.repository_tooling.repository_understanding_material import (
    question_audit as question_audit,
)
from oh_goon.repository_tooling.repository_understanding_material import (
    question_lifecycle as question_lifecycle,
)
from oh_goon.repository_tooling.repository_understanding_material import verify as verify
from oh_goon.repository_tooling.repository_understanding_policy import (
    DEFAULT_QUALIFICATION_PROFILE,
    QUALIFICATION_POLICY_VERSION,
    QUALIFICATION_PROFILES,
    SCORING_POLICY_VERSION,
    evaluate,
)
from oh_goon.repository_tooling.repository_understanding_recertification import (
    differential_recertification_plan as differential_recertification_plan,
)
from oh_goon.repository_tooling.repository_understanding_stability import (
    stability_report as stability_report,
)

build_challenge_set = challenge_set


def grade_template(
    *,
    submission: Path,
    candidate: str | None = None,
    mode: str | None = None,
    qualification_profile: str = DEFAULT_QUALIFICATION_PROFILE,
    candidate_configuration: Path | None = None,
    candidate_run_identity: str | None = None,
) -> dict[str, Any]:
    if qualification_profile not in QUALIFICATION_PROFILES:
        raise ValueError(
            "qualification profile must be one of " + ", ".join(sorted(QUALIFICATION_PROFILES))
        )
    exam_manifest = manifest()
    snapshot = exam_snapshot()
    score_question_ids = {
        row["id"]
        for row in snapshot["question_lifecycle"]["questions"]
        if row.get("qualification_bank_member") is True and row.get("score_bearing") is True
    }
    submission_digest, submission_bundle = _validate_candidate_submission(submission)
    configuration_bundle = (
        None
        if candidate_configuration is None
        else _candidate_configuration_bundle(candidate_configuration)
    )
    if candidate_run_identity is not None:
        candidate_run_identity = candidate_run_identity.strip()
        if not candidate_run_identity or len(candidate_run_identity) > 128:
            raise ValueError(
                "candidate run identity must be a non-empty string of at most 128 characters"
            )
    return {
        "artifact_type": "repository-understanding-grader-observations",
        "source_version": _source_version(),
        "qa_schema_version": QA_SCHEMA_VERSION,
        "question_schema_version": QUESTION_SCHEMA_VERSION,
        "grader_schema_version": GRADER_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
        "qualification_profile": qualification_profile,
        "exam_manifest_sha256": exam_manifest["manifest_sha256"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "blueprint_audit": snapshot.get("blueprint_audit"),
        "question_audit_report": snapshot.get("question_audit_report"),
        "relationship_graph_policy": graph_manifest(),
        "candidate_submission_sha256": submission_digest,
        "candidate_submission": submission_bundle,
        "candidate_configuration_identity": (
            None if configuration_bundle is None else configuration_bundle["sha256"]
        ),
        "candidate_configuration": configuration_bundle,
        "candidate_run_identity": candidate_run_identity,
        "candidate": candidate,
        "mode": mode,
        "grader": {
            "type": None,
            "identity": None,
            "rubric_version": RUBRIC_VERSION,
            "configuration": None,
        },
        "graded_at_utc": None,
        "exam_snapshot": snapshot,
        "grades": [
            {
                "id": question.id,
                "question_status": None,
                "reference_status": None,
                "claims": [
                    {
                        "claim_id": claim.id,
                        "result": None,
                        "confidence": None,
                        "candidate_epistemic_state": None,
                        "candidate_confidence": None,
                        "evidence": {
                            "references": [],
                            "relevance": None,
                            "support": None,
                            "authority": None,
                        },
                        "rationale": None,
                    }
                    for claim in question.expected_claims
                ],
                "contradictions": [
                    {
                        "contradiction_id": item.id,
                        "observed": None,
                        "confidence": None,
                        "rationale": None,
                    }
                    for item in question.contradictions
                ],
                "assertions": [],
                "rationale": None,
            }
            for question in load_exam()
            if question.id in score_question_ids
        ],
    }


def score(grades_path: Path, *, submission: Path | None = None) -> dict[str, Any]:
    payload = json.loads(grades_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("grade input must be a JSON object")
    (
        snapshot,
        observations,
        evidence_validation,
        grader,
        graded_at,
        candidate_submission,
        qualification_profile,
        candidate_configuration,
        candidate_configuration_identity,
        candidate_run_identity,
        regraded,
    ) = _score_inputs(payload, submission)
    policy_result = evaluate(
        exam_snapshot=snapshot,
        observations=observations,
        evidence_validation=evidence_validation,
        grader=grader,
        graded_at_utc=graded_at,
        qualification_profile_id=qualification_profile,
    )
    result = {
        "artifact_type": "repository-understanding-result",
        "qa_schema_version": QA_SCHEMA_VERSION,
        "question_schema_version": QUESTION_SCHEMA_VERSION,
        "grader_schema_version": GRADER_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "source_version": snapshot["source_version"],
        "repository_identity": snapshot["repository_identity"],
        "exam_snapshot_sha256": snapshot["snapshot_sha256"],
        "blueprint_audit": snapshot.get("blueprint_audit"),
        "question_audit_report": snapshot.get("question_audit_report"),
        "relationship_graph_policy": graph_manifest(),
        "candidate_submission_sha256": candidate_submission["sha256"],
        "candidate_configuration_identity": candidate_configuration_identity,
        "candidate_run_identity": candidate_run_identity,
        "candidate": payload.get("candidate") if not regraded else payload.get("candidate"),
        "mode": payload.get("mode") if not regraded else payload.get("mode"),
        "regraded": regraded,
        **policy_result,
        "regrade_bundle": {
            "exam_snapshot": snapshot,
            "candidate_submission": candidate_submission,
            "qualification_profile": qualification_profile,
            "candidate_configuration": candidate_configuration,
            "candidate_configuration_identity": candidate_configuration_identity,
            "candidate_run_identity": candidate_run_identity,
            "grader_observations": observations,
            "evidence_validation": evidence_validation,
            "grader": grader,
            "graded_at_utc": graded_at,
        },
    }
    result["certificate_identity"] = "sha256:" + _canonical_json_sha256(
        {
            key: value
            for key, value in result.items()
            if key not in {"certificate_identity", "regrade_bundle", "regraded"}
        }
    )
    return result


def main() -> int:
    from oh_goon.repository_tooling._repository_understanding_exam_cli import build_parser, dispatch

    return dispatch(build_parser(description=__doc__).parse_args(), globals())


if __name__ == "__main__":
    raise SystemExit(main())
