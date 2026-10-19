"""Concept-bound differential Repository Understanding recertification planning.

A differential plan is evidence only.  Exact-source capability certificates remain stale as soon
as repository identity changes.  This module compares the prior certified exam snapshot with the
current snapshot and identifies the smallest declared concept/question surface that needs fresh
certification evidence.  It never grants or carries forward runtime permissions by itself.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling.repository_understanding_capability import (
    CAPABILITY_POLICY_VERSION,
    validate_capability_certificate,
)
from oh_goon.repository_tooling.repository_understanding_stability import validated_result

DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION = (
    "repository-understanding-differential-recertification.v1"
)
DIFFERENTIAL_RECERTIFICATION_SCHEMA_VERSION = 1


def _canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _identity(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{label} must be a sha256 identity")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a sha256 identity") from exc
    return value


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a 64-character sha256 digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a 64-character sha256 digest") from exc
    return value


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _validated_snapshot(payload: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an exam snapshot object")
    snapshot = deepcopy(payload)
    digest = _digest(snapshot.get("snapshot_sha256"), label=f"{label} snapshot_sha256")
    base = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    if _canonical_sha256(base) != digest:
        raise ValueError(f"{label} snapshot_sha256 does not match snapshot bytes")
    _identity(snapshot.get("repository_identity"), label=f"{label} repository_identity")
    questions = snapshot.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"{label} must contain questions")
    return snapshot


def _question_binding(question: dict[str, Any]) -> dict[str, Any]:
    qid = question.get("id")
    public = question.get("public")
    secret = question.get("secret")
    if not isinstance(qid, str) or not isinstance(public, dict) or not isinstance(secret, dict):
        raise ValueError("exam snapshot contains invalid question binding")
    blueprint = public.get("blueprint")
    if not isinstance(blueprint, dict) or not isinstance(blueprint.get("concept_family"), str):
        raise ValueError(f"{qid} is missing concept_family")
    evidence = secret.get("evidence_reference_identities")
    if not isinstance(evidence, list):
        raise ValueError(f"{qid} is missing evidence reference identities")
    range_bindings: list[dict[str, Any]] = []
    for row in evidence:
        if not isinstance(row, dict):
            raise ValueError(f"{qid} evidence identity must be an object")
        reference = row.get("reference")
        range_sha = row.get("range_sha256")
        if not isinstance(reference, str):
            raise ValueError(f"{qid} evidence identity is missing reference")
        _identity(range_sha, label=f"{qid} evidence range_sha256")
        range_bindings.append({"reference": reference, "range_sha256": range_sha})
    lifecycle = secret.get("question_lifecycle")
    if not isinstance(lifecycle, dict) or not isinstance(lifecycle.get("state"), str):
        raise ValueError(f"{qid} is missing question lifecycle state")
    base = {
        "id": qid,
        "section": question.get("section"),
        "severity": question.get("severity"),
        "public": public,
        "lifecycle_state": lifecycle["state"],
        "expected_claims": secret.get("expected_claims"),
        "contradictions": secret.get("contradictions"),
        "evidence_expectations": secret.get("evidence_expectations"),
        "evidence_range_bindings": range_bindings,
        "evidence_authority_roles": secret.get("evidence_authority_roles"),
    }
    return {
        **base,
        "question_binding_identity": "sha256:" + _canonical_sha256(base),
        "concept_family": blueprint["concept_family"],
    }


def concept_bindings(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Derive deterministic concept bindings from declared semantic/evidence dependencies."""

    validated = _validated_snapshot(snapshot, label="exam")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in validated["questions"]:
        if not isinstance(raw, dict):
            raise ValueError("exam snapshot question must be an object")
        row = _question_binding(raw)
        grouped.setdefault(str(row["concept_family"]), []).append(row)
    output: dict[str, dict[str, Any]] = {}
    for concept in sorted(grouped):
        rows = sorted(grouped[concept], key=lambda row: str(row["id"]))
        base = {
            "concept_family": concept,
            "questions": [
                {
                    "id": row["id"],
                    "lifecycle_state": row["lifecycle_state"],
                    "question_binding_identity": row["question_binding_identity"],
                }
                for row in rows
            ],
        }
        output[concept] = {**base, "concept_binding_identity": "sha256:" + _canonical_sha256(base)}
    return output


def _changed_paths(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old = previous.get("repository_evidence_index")
    new = current.get("repository_evidence_index")
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError("exam snapshots must contain repository_evidence_index")
    changed: list[str] = []
    for path in sorted(set(old) | set(new)):
        if old.get(path) != new.get(path):
            changed.append(path)
    return changed


def _declared_evidence_paths(snapshot: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for question in snapshot["questions"]:
        secret = question.get("secret") if isinstance(question, dict) else None
        refs = secret.get("evidence_expectations") if isinstance(secret, dict) else None
        if not isinstance(refs, list):
            continue
        for reference in refs:
            if isinstance(reference, str) and ":" in reference:
                paths.add(
                    reference.rsplit(":", 1)[0]
                    if reference.rsplit(":", 1)[1].isdigit()
                    else reference.split(":", 1)[0]
                )
    return paths


def differential_recertification_plan(
    certificate_path: Path,
    prior_result_path: Path,
    *,
    current_snapshot: dict[str, Any],
) -> dict[str, Any]:
    certificate = _load_json(certificate_path, label="capability certificate")
    validate_capability_certificate(certificate)
    if certificate.get("capability_policy_version") != CAPABILITY_POLICY_VERSION:
        raise ValueError("capability certificate policy is not current")

    prior_result = validated_result(prior_result_path)
    if prior_result.get("certificate_identity") not in certificate.get(
        "run_certificate_identities", []
    ):
        raise ValueError("prior result is not one of the capability certificate qualifying runs")
    if prior_result.get("repository_identity") != certificate.get("repository_identity"):
        raise ValueError("prior result repository identity does not match capability certificate")
    if prior_result.get("exam_snapshot_sha256") != certificate.get("exam_snapshot_sha256"):
        raise ValueError("prior result exam snapshot does not match capability certificate")
    if prior_result.get("candidate_configuration_identity") != certificate.get(
        "candidate_configuration_identity"
    ):
        raise ValueError(
            "prior result candidate configuration does not match capability certificate"
        )

    bundle = prior_result.get("regrade_bundle")
    if not isinstance(bundle, dict):
        raise ValueError("prior result is missing regrade_bundle")
    previous = _validated_snapshot(bundle.get("exam_snapshot"), label="prior exam")
    current = _validated_snapshot(current_snapshot, label="current exam")
    if previous.get("snapshot_sha256") != prior_result.get("exam_snapshot_sha256"):
        raise ValueError("prior result embedded snapshot identity is inconsistent")

    old_bindings = concept_bindings(previous)
    new_bindings = concept_bindings(current)
    old_concepts = set(old_bindings)
    new_concepts = set(new_bindings)
    preserved = sorted(
        concept
        for concept in old_concepts & new_concepts
        if old_bindings[concept]["concept_binding_identity"]
        == new_bindings[concept]["concept_binding_identity"]
    )
    affected = sorted((old_concepts & new_concepts) - set(preserved))
    added = sorted(new_concepts - old_concepts)
    removed = sorted(old_concepts - new_concepts)

    recertify_concepts = sorted(set(affected) | set(added))
    recertify_questions: list[str] = []
    for concept in recertify_concepts:
        for question in new_bindings[concept]["questions"]:
            if question["lifecycle_state"] == "ACTIVE":
                recertify_questions.append(str(question["id"]))
    recertify_questions = sorted(set(recertify_questions), key=lambda qid: int(qid[1:]))

    changed_paths = _changed_paths(previous, current)
    declared_paths = _declared_evidence_paths(previous) | _declared_evidence_paths(current)
    mapped_changed_paths = sorted(path for path in changed_paths if path in declared_paths)
    unmapped_changed_paths = sorted(path for path in changed_paths if path not in declared_paths)

    source_changed = previous["repository_identity"] != current["repository_identity"]
    if not source_changed and not affected and not added and not removed:
        status = "NO_RECERTIFICATION_REQUIRED"
    elif not affected and not added and not removed:
        status = "SOURCE_CHANGED_DECLARED_CONCEPT_BINDINGS_UNCHANGED"
    else:
        status = "DIFFERENTIAL_RECERTIFICATION_REQUIRED"

    base = {
        "artifact_type": "repository-understanding-differential-recertification-plan",
        "schema_version": DIFFERENTIAL_RECERTIFICATION_SCHEMA_VERSION,
        "policy_version": DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION,
        "capability_policy_version": CAPABILITY_POLICY_VERSION,
        "previous_capability_certificate_identity": certificate["capability_certificate_identity"],
        "previous_certificate_status": certificate["certificate_status"],
        "previous_granted_tier": certificate.get("granted_tier"),
        "candidate_configuration_identity": certificate["candidate_configuration_identity"],
        "previous_source_version": previous.get("source_version"),
        "current_source_version": current.get("source_version"),
        "previous_repository_identity": previous["repository_identity"],
        "current_repository_identity": current["repository_identity"],
        "previous_exam_snapshot_sha256": previous["snapshot_sha256"],
        "current_exam_snapshot_sha256": current["snapshot_sha256"],
        "source_changed": source_changed,
        "status": status,
        "capability_state": "SUSPENDED_SOURCE_CHANGED"
        if source_changed
        else certificate["certificate_status"],
        "preserved_concepts": preserved,
        "affected_concepts": affected,
        "new_concepts": added,
        "removed_concepts": removed,
        "recertification_concepts": recertify_concepts,
        "recertification_question_ids": recertify_questions,
        "changed_repository_paths": changed_paths,
        "mapped_changed_paths": mapped_changed_paths,
        "unmapped_changed_paths": unmapped_changed_paths,
        "dependency_model": "DECLARED_SECRET_EVIDENCE_RANGES_V1",
        "dependency_rule": (
            "concept invalidation compares question semantics/lifecycle plus exact declared evidence range bytes; "
            "whole-file changes outside a cited range are diagnostic rather than automatic semantic invalidation"
        ),
        "carry_forward_rule": (
            "preserved concept evidence may reduce the next exam scope, but this plan never extends the old exact-source certificate"
        ),
        "enforcement_status": "EVIDENCE_ONLY",
        "reissue_required": source_changed,
        "reissue_note": (
            "an execution/admission authority must continue to reject the old certificate for new repository bytes; "
            "fresh governed recertification/reissue evidence is required before capability can apply to the new source"
        ),
    }
    return {**base, "recertification_plan_identity": "sha256:" + _canonical_sha256(base)}


def validate_recertification_plan(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("artifact_type") != "repository-understanding-differential-recertification-plan":
        raise ValueError("differential recertification artifact_type mismatch")
    if payload.get("schema_version") != DIFFERENTIAL_RECERTIFICATION_SCHEMA_VERSION:
        raise ValueError("differential recertification schema mismatch")
    if payload.get("policy_version") != DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION:
        raise ValueError("differential recertification policy mismatch")
    identity = _identity(
        payload.get("recertification_plan_identity"), label="recertification_plan_identity"
    )
    base = {key: value for key, value in payload.items() if key != "recertification_plan_identity"}
    if identity != "sha256:" + _canonical_sha256(base):
        raise ValueError("differential recertification identity does not match artifact bytes")
    if payload.get("enforcement_status") != "EVIDENCE_ONLY":
        raise ValueError("differential recertification cannot become runtime authority")
    if (
        payload.get("source_changed")
        and payload.get("capability_state") != "SUSPENDED_SOURCE_CHANGED"
    ):
        raise ValueError("source-changed recertification plan must suspend exact-source capability")
    return payload


def recertification_policy_manifest() -> dict[str, Any]:
    return {
        "policy_version": DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION,
        "schema_version": DIFFERENTIAL_RECERTIFICATION_SCHEMA_VERSION,
        "dependency_model": "DECLARED_SECRET_EVIDENCE_RANGES_V1",
        "source_change_behavior": "suspend exact-source capability before differential analysis",
        "carry_forward": "preserved concepts may narrow the next exam; plan itself never reauthorizes capability",
        "unmapped_changes": "diagnostic only in v1; they do not silently prove semantic irrelevance",
        "enforcement_status": "EVIDENCE_ONLY",
    }
