from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling.repository_understanding_graph import evidence_authority_assessment, evidence_profile
from oh_goon.repository_tooling.repository_understanding_material import (
    CANDIDATE_GOLD_MARKER, EVIDENCE_REFERENCE, GRADER_SCHEMA_VERSION, QA_SCHEMA_VERSION, QUESTION_SCHEMA_VERSION, RUBRIC_VERSION, SECRET_SCHEMA_VERSION, _canonical_json_sha256,
)
from oh_goon.repository_tooling.repository_understanding_policy import QUALIFICATION_POLICY_VERSION, QUALIFICATION_PROFILES, SCORING_POLICY_VERSION
from oh_goon.repository_tooling._repository_understanding_exam_inputs import _validate_candidate_submission

def _validated_graded_at(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("grade input must contain graded_at_utc")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("graded_at_utc must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("graded_at_utc must be UTC")
    return text



def _validated_grader(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("grade input must identify the grader")
    grader_type = value.get("type")
    identity = value.get("identity")
    if not isinstance(grader_type, str) or not grader_type.strip():
        raise ValueError("grade input must identify grader type")
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("grade input must identify grader identity")
    if value.get("rubric_version") != RUBRIC_VERSION:
        raise ValueError(f"grader rubric_version must be {RUBRIC_VERSION}")
    configuration = value.get("configuration")
    if configuration is not None and not isinstance(configuration, (str, dict)):
        raise ValueError("grader configuration must be a string, object, or null")
    return {
        "type": grader_type.strip(),
        "identity": identity.strip(),
        "rubric_version": RUBRIC_VERSION,
        "configuration": configuration,
    }



def _validate_snapshot(snapshot: Any, expected_sha256: str | None = None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("grade input must contain an exam_snapshot")
    supplied_sha = snapshot.get("snapshot_sha256")
    base = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    observed_sha = _canonical_json_sha256(base)
    if supplied_sha != observed_sha:
        raise ValueError("exam_snapshot_sha256 does not match embedded snapshot")
    if expected_sha256 is not None and expected_sha256 != supplied_sha:
        raise ValueError("grade exam_snapshot_sha256 does not match embedded snapshot")
    expected_versions = {
        "qa_schema_version": QA_SCHEMA_VERSION,
        "question_schema_version": QUESTION_SCHEMA_VERSION,
        "secret_schema_version": SECRET_SCHEMA_VERSION,
        "grader_schema_version": GRADER_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
    }
    for field, expected in expected_versions.items():
        if snapshot.get(field) != expected:
            raise ValueError(f"embedded {field} must be {expected}")
    return snapshot



def _candidate_reference_validation(reference: str, index: dict[str, Any]) -> bool:
    match = EVIDENCE_REFERENCE.fullmatch(reference)
    if match is None:
        return False
    path = match.group("path")
    if path.startswith(CANDIDATE_GOLD_MARKER):
        return False
    metadata = index.get(path)
    if not isinstance(metadata, dict):
        return False
    line_count = metadata.get("line_count")
    if type(line_count) is not int or line_count < 1:
        return False
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    return 1 <= start <= end <= line_count



def _evidence_validation(
    snapshot: dict[str, Any], observations: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    index = snapshot.get("repository_evidence_index")
    if not isinstance(index, dict):
        raise ValueError("exam snapshot must contain repository_evidence_index")
    snapshot_by_id = {question["id"]: question for question in snapshot.get("questions", [])}
    results: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, dict) or not isinstance(observation.get("id"), str):
            raise ValueError("every grader observation must contain a question id")
        qid = observation["id"]
        question = snapshot_by_id.get(qid)
        if not isinstance(question, dict):
            raise ValueError(f"unknown grader observation question id: {qid}")
        claims = observation.get("claims")
        if not isinstance(claims, list):
            raise ValueError(f"{qid} claims must be a list")
        expected_claim_ids = [claim["id"] for claim in question["secret"]["expected_claims"]]
        by_claim_id = {claim.get("claim_id"): claim for claim in claims if isinstance(claim, dict)}
        if list(by_claim_id) != expected_claim_ids:
            raise ValueError(f"{qid} claim observations must match expected claims in canonical order")
        claim_results: dict[str, dict[str, Any]] = {}
        for claim_id in expected_claim_ids:
            evidence = by_claim_id[claim_id].get("evidence")
            if not isinstance(evidence, dict):
                raise ValueError(f"{qid}/{claim_id} evidence must be an object")
            references = evidence.get("references")
            if not isinstance(references, list) or not all(isinstance(ref, str) for ref in references):
                raise ValueError(f"{qid}/{claim_id} evidence references must be a list of strings")
            if not references:
                minimum_authority = question["secret"]["evidence_authority_roles"][
                    "canonical_minimum_authority"
                ]
                if not isinstance(minimum_authority, str):
                    raise ValueError(f"{qid} has no canonical evidence authority")
                claim_results[claim_id] = {
                    "existence": "MISSING",
                    "references": [],
                    "invalid_references": [],
                    "evidence_profile": evidence_profile([]),
                    "authority_assessment": evidence_authority_assessment(
                        [], minimum_authority
                    ),
                }
                continue
            invalid = [ref for ref in references if not _candidate_reference_validation(ref, index)]
            valid_references = [ref for ref in references if ref not in invalid]
            minimum_authority = question["secret"]["evidence_authority_roles"][
                "canonical_minimum_authority"
            ]
            if not isinstance(minimum_authority, str):
                raise ValueError(f"{qid} has no canonical evidence authority")
            claim_results[claim_id] = {
                "existence": "FABRICATED" if invalid else "VALID",
                "references": references,
                "invalid_references": invalid,
                "evidence_profile": evidence_profile(valid_references),
                "authority_assessment": evidence_authority_assessment(
                    valid_references, minimum_authority
                ),
            }
        results[qid] = {"claims": claim_results}
    return results



def _score_inputs(payload: dict[str, Any], submission: Path | None) -> tuple[
    dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any], str,
    dict[str, Any], str, dict[str, Any] | None, str | None, str | None, bool
]:
    if payload.get("artifact_type") == "repository-understanding-result":
        bundle = payload.get("regrade_bundle")
        if not isinstance(bundle, dict):
            raise ValueError("result artifact is missing regrade_bundle")
        snapshot = _validate_snapshot(bundle.get("exam_snapshot"))
        observations = bundle.get("grader_observations")
        evidence_validation = bundle.get("evidence_validation")
        grader = _validated_grader(bundle.get("grader"))
        graded_at = _validated_graded_at(bundle.get("graded_at_utc"))
        candidate_submission = bundle.get("candidate_submission")
        qualification_profile = bundle.get("qualification_profile")
        candidate_configuration = bundle.get("candidate_configuration")
        candidate_configuration_identity = bundle.get("candidate_configuration_identity")
        candidate_run_identity = bundle.get("candidate_run_identity")
        if qualification_profile not in QUALIFICATION_PROFILES:
            raise ValueError("regrade_bundle qualification_profile is invalid")
        if not isinstance(observations, list) or not isinstance(evidence_validation, dict):
            raise ValueError("regrade_bundle is incomplete")
        if not isinstance(candidate_submission, dict):
            raise ValueError("regrade_bundle candidate_submission is missing")
        if candidate_configuration is not None:
            if not isinstance(candidate_configuration, dict) or candidate_configuration.get("sha256") != candidate_configuration_identity:
                raise ValueError("regrade_bundle candidate configuration identity is inconsistent")
        elif candidate_configuration_identity is not None:
            raise ValueError("regrade_bundle candidate configuration is missing")
        if candidate_run_identity is not None and (
            not isinstance(candidate_run_identity, str) or not candidate_run_identity.strip() or len(candidate_run_identity) > 128
        ):
            raise ValueError("regrade_bundle candidate run identity is invalid")
        return (
            snapshot, observations, evidence_validation, grader, graded_at, candidate_submission,
            qualification_profile, candidate_configuration, candidate_configuration_identity, candidate_run_identity, True
        )

    if payload.get("artifact_type") != "repository-understanding-grader-observations":
        raise ValueError("grade input has unsupported artifact_type")
    expected_versions = {
        "qa_schema_version": QA_SCHEMA_VERSION,
        "question_schema_version": QUESTION_SCHEMA_VERSION,
        "grader_schema_version": GRADER_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
    }
    for field, expected in expected_versions.items():
        if payload.get(field) != expected:
            raise ValueError(f"grade {field} must be {expected}")
    snapshot = _validate_snapshot(payload.get("exam_snapshot"), payload.get("exam_snapshot_sha256"))
    observations = payload.get("grades")
    if not isinstance(observations, list):
        raise ValueError("grade input must contain grader observations")
    candidate_submission = payload.get("candidate_submission")
    if not isinstance(candidate_submission, dict):
        raise ValueError("grade input must preserve candidate_submission")
    embedded_digest = candidate_submission.get("sha256")
    if embedded_digest != payload.get("candidate_submission_sha256"):
        raise ValueError("embedded candidate submission identity is inconsistent")
    if submission is not None:
        supplied_digest, _bundle = _validate_candidate_submission(submission)
        if supplied_digest != embedded_digest:
            raise ValueError("grade sheet is not bound to the supplied candidate submission")
    candidate_configuration = payload.get("candidate_configuration")
    candidate_configuration_identity = payload.get("candidate_configuration_identity")
    candidate_run_identity = payload.get("candidate_run_identity")
    if candidate_configuration is not None:
        if not isinstance(candidate_configuration, dict) or candidate_configuration.get("sha256") != candidate_configuration_identity:
            raise ValueError("candidate configuration identity is inconsistent")
    elif candidate_configuration_identity is not None:
        raise ValueError("candidate configuration bundle is missing")
    if candidate_run_identity is not None and (
        not isinstance(candidate_run_identity, str) or not candidate_run_identity.strip() or len(candidate_run_identity) > 128
    ):
        raise ValueError("candidate run identity is invalid")
    grader = _validated_grader(payload.get("grader"))
    graded_at = _validated_graded_at(payload.get("graded_at_utc"))
    qualification_profile = payload.get("qualification_profile")
    if qualification_profile not in QUALIFICATION_PROFILES:
        raise ValueError(
            "qualification profile must be one of " + ", ".join(sorted(QUALIFICATION_PROFILES))
        )
    evidence_validation = _evidence_validation(snapshot, observations)
    return (
        snapshot, observations, evidence_validation, grader, graded_at, candidate_submission,
        qualification_profile, candidate_configuration, candidate_configuration_identity, candidate_run_identity, False
    )
