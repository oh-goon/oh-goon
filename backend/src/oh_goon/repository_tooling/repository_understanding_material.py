"""Canonical PUBLIC/EVIDENCE/SECRET repository-understanding exam material.

This module owns question/SECRET parsing, evidence validation, blind checkout preparation,
and frozen exam snapshots. It does not grade candidate answers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from oh_goon.repository_tooling.release.package_slim import (
    canonical_source_identity,
)
from oh_goon.repository_tooling.repository_understanding_blueprint import (
    QUESTION_FORMS as BLUEPRINT_QUESTION_FORMS,
)
from oh_goon.repository_tooling.repository_understanding_blueprint import (
    assert_exam_release_ready,
)
from oh_goon.repository_tooling.repository_understanding_calibration import (
    CALIBRATION_POLICY_VERSION,
    CALIBRATION_PROMOTION_REVIEW_POLICY_VERSION,
    QUESTION_AUDIT_POLICY_VERSION,
    assert_question_audit_release_ready,
    load_question_audit,
)
from oh_goon.repository_tooling.repository_understanding_capability import (
    CAPABILITY_POLICY_VERSION,
    capability_policy_manifest,
)
from oh_goon.repository_tooling.repository_understanding_game_tape import (
    GAME_TAPE_POLICY_VERSION,
    game_tape_policy_manifest,
)
from oh_goon.repository_tooling.repository_understanding_graph import (
    graph_spec,
)
from oh_goon.repository_tooling.repository_understanding_integrity import (
    ADVERSARIAL_POLICY_VERSION,
    BENCHMARK_INTEGRITY_POLICY_VERSION,
    benchmark_integrity_manifest,
    load_benchmark_registry,
)
from oh_goon.repository_tooling.repository_understanding_lifecycle import (
    HUMAN_BASELINE_POLICY_VERSION,
    QUESTION_LIFECYCLE_POLICY_VERSION,
    assert_current_score_bank_active,
    load_question_lifecycle,
)
from oh_goon.repository_tooling.repository_understanding_policy import (
    ASSERTION_RELATIONS,
    CANDIDATE_CONFIDENCE_STATES,
    CANDIDATE_EPISTEMIC_STATES,
    CLAIM_STATES,
    DIMENSION_NORMALIZATION_POLICY_VERSION,
    QUALIFICATION_POLICY_VERSION,
    QUESTION_STATES,
    REFERENCE_STATES,
    SCORING_POLICY_VERSION,
    qualification_policy_manifest,
)
from oh_goon.repository_tooling.repository_understanding_policy import (
    DIMENSION_WEIGHTS as DIMENSION_WEIGHTS,
)
from oh_goon.repository_tooling.repository_understanding_recertification import (
    DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION,
    recertification_policy_manifest,
)
from oh_goon.repository_tooling.repository_understanding_stability import (
    STABILITY_POLICY_VERSION,
    stability_policy_manifest,
)
from oh_goon.repository_tooling.source_layout import (
    is_transient_source_path,
)

ROOT = Path(__file__).resolve().parents[4]
CERTIFICATION_RELATIVE = Path("docs/certification")
QUESTION_RELATIVE = CERTIFICATION_RELATIVE / "questions"
ANSWER_RELATIVE = CERTIFICATION_RELATIVE / "answers"
QUESTION_METADATA_RELATIVE = QUESTION_RELATIVE / "question-metadata.yml"
QUESTION_AUDIT_RELATIVE = ANSWER_RELATIVE / "question-audit.yml"
QUESTION_LIFECYCLE_RELATIVE = ANSWER_RELATIVE / "question-lifecycle.yml"
BENCHMARK_REGISTRY_RELATIVE = ANSWER_RELATIVE / "benchmark-integrity.yml"
QUESTION_ID = re.compile(r"^## (Q\d{2,3})\s*$", re.MULTILINE)
EVIDENCE_REFERENCE = re.compile(r"^(?P<path>[^:]+):(?P<start>\d+)(?:-(?P<end>\d+))?$")
INLINE_SOURCE_REFERENCE = re.compile(r"[A-Za-z0-9_./-]+:\d+(?:-\d+)?")
QUESTION_TYPES = (
    "FACT",
    "RELATIONSHIP",
    "EXPLANATION",
    "COUNTERFACTUAL",
    "CHANGE_IMPACT",
    "FALSE_PREMISE",
)
DIFFICULTIES = ("L1", "L2", "L3", "L4", "L5")
DIMENSIONS = (
    "structure",
    "execution",
    "ownership",
    "lifecycle",
    "contracts",
    "testing",
    "change_impact",
    "evidence_quality",
    "epistemic_accuracy",
    "mental_model_consistency",
)
MISCONCEPTIONS = (
    "caller_owner_confusion",
    "observer_authority_confusion",
    "desired_observed_conflation",
    "absence_as_positive_evidence",
    "test_as_runtime_authority",
    "duplicate_owner_assumption",
    "hidden_fallback_assumption",
    "identity_scope_confusion",
    "lifecycle_state_confusion",
    "dependency_direction_reversal",
    "semantic_invariant_violation",
)
BROAD_MISCONCEPTIONS = (
    "caller_owner_confusion",
    "semantic_invariant_violation",
)
QA_SCHEMA_VERSION = 28
QUESTION_SCHEMA_VERSION = 2
SECRET_SCHEMA_VERSION = 1
GRADER_SCHEMA_VERSION = 6
RUBRIC_VERSION = "repository-understanding.v17"
MISCONCEPTION_COVERAGE_POLICY_VERSION = "repository-misconception-coverage.v7"
CANDIDATE_GOLD_MARKER = "docs/certification/answers"
ANSWERABILITY_POLICY_VERSION = "repository-understanding-external-answerability.v1"
QUESTION_HEALTH_MAINTENANCE_POLICY_VERSION = (
    "repository-understanding-question-health-maintenance.v1"
)


@dataclass(frozen=True)
class ExpectedClaim:
    id: str
    statement: str
    critical: bool


@dataclass(frozen=True)
class Contradiction:
    id: str
    statement: str
    severity: str
    misconception_id: str


@dataclass(frozen=True)
class ExamQuestion:
    id: str
    section: str
    severity: str
    prompt: str
    type: str
    dimension: str
    difficulty: str
    response_expectations: dict[str, bool]
    evidence_scope: tuple[str, ...]
    blueprint_dimension: str
    blueprint_form: str
    expected_claims: tuple[ExpectedClaim, ...]
    contradictions: tuple[Contradiction, ...]
    evidence_expectations: tuple[str, ...]


def _source_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _canonical_json_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _ignore(directory: str, names: list[str]) -> set[str]:
    current = Path(directory)
    try:
        relative_dir = current.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"exam copy escaped source root: {current}") from exc
    ignored = {name for name in names if is_transient_source_path(relative_dir / name)}
    answers = ANSWER_RELATIVE
    if (
        PurePosixPath(*relative_dir.parts) == PurePosixPath(*answers.parent.parts)
        and answers.name in names
    ):
        ignored.add(answers.name)
    return ignored


def _validate_source_symlinks(root: Path = ROOT) -> None:
    """Fail closed on non-transient source symlinks before blind-copy preparation."""

    root = root.resolve()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        dirnames[:] = [
            name
            for name in dirnames
            if not is_transient_source_path((current / name).relative_to(root))
        ]
        for name in [*dirnames, *filenames]:
            path = current / name
            if path.is_symlink():
                raise ValueError(
                    "repository-understanding exam source must not contain symlinks: "
                    f"{path.relative_to(root)}"
                )


def _question_prompts() -> dict[str, str]:
    prompts: dict[str, str] = {}
    for path in sorted((ROOT / QUESTION_RELATIVE).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        matches = list(QUESTION_ID.finditer(text))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            prompt = text[match.end() : end].strip()
            if not prompt:
                raise ValueError(f"{match.group(1)} has an empty PUBLIC prompt")
            if match.group(1) in prompts:
                raise ValueError(f"duplicate PUBLIC question id: {match.group(1)}")
            prompts[match.group(1)] = prompt
    return prompts


def _metadata() -> dict[str, dict[str, Any]]:
    path = ROOT / QUESTION_METADATA_RELATIVE
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != QUESTION_SCHEMA_VERSION:
        raise ValueError("PUBLIC question metadata schema_version mismatch")
    if payload.get("source_version") != _source_version():
        raise ValueError("PUBLIC question metadata source_version mismatch")
    entries = payload.get("questions")
    if not isinstance(entries, list) or not entries:
        raise ValueError("PUBLIC question metadata must contain questions")
    by_id: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("PUBLIC question metadata entry must contain id")
        qid = item["id"]
        if qid in by_id:
            raise ValueError(f"duplicate PUBLIC metadata id: {qid}")
        if item.get("type") not in QUESTION_TYPES:
            raise ValueError(f"{qid} has invalid question type")
        if item.get("dimension") not in DIMENSIONS:
            raise ValueError(f"{qid} has invalid dimension")
        if item.get("difficulty") not in DIFFICULTIES:
            raise ValueError(f"{qid} has invalid difficulty")
        blueprint_dimension = item.get("blueprint_dimension")
        from oh_goon.repository_tooling.repository_understanding_blueprint import (
            DIMENSION_TARGET_RANGES,
        )

        if blueprint_dimension not in DIMENSION_TARGET_RANGES:
            raise ValueError(f"{qid} has invalid blueprint_dimension")
        if item.get("blueprint_form") not in BLUEPRINT_QUESTION_FORMS:
            raise ValueError(f"{qid} has invalid blueprint_form")
        expectations = item.get("response_expectations")
        if expectations != {"require_evidence": True, "allow_insufficient_evidence": True}:
            raise ValueError(
                f"{qid} response_expectations must use the canonical candidate contract"
            )
        scope = item.get("evidence_scope")
        if (
            not isinstance(scope, list)
            or not scope
            or not all(isinstance(value, str) for value in scope)
        ):
            raise ValueError(f"{qid} must declare broad EVIDENCE scope")
        body = json.dumps(item, sort_keys=True)
        if CANDIDATE_GOLD_MARKER in body or INLINE_SOURCE_REFERENCE.search(body):
            raise ValueError(f"{qid} PUBLIC metadata leaks SECRET evidence")
        by_id[qid] = item
    return by_id


def _answer_payloads() -> list[tuple[Path, dict[str, Any]]]:
    payloads: list[tuple[Path, dict[str, Any]]] = []
    special = {
        ROOT / QUESTION_AUDIT_RELATIVE,
        ROOT / QUESTION_LIFECYCLE_RELATIVE,
        ROOT / BENCHMARK_REGISTRY_RELATIVE,
    }
    for path in sorted((ROOT / ANSWER_RELATIVE).glob("*.yml")):
        if path in special:
            continue
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"SECRET answer file must contain a mapping: {path.relative_to(ROOT)}")
        payloads.append((path, payload))
    return payloads


def _validate_evidence(reference: str, *, root: Path = ROOT) -> None:
    match = EVIDENCE_REFERENCE.fullmatch(reference)
    if match is None:
        raise ValueError(f"invalid evidence reference: {reference!r}")
    relative = Path(match.group("path"))
    source = (root / relative).resolve()
    if not _contained(source, root) or not source.is_file():
        raise ValueError(f"evidence source does not exist inside repository: {reference!r}")
    if _contained(source, (root / ANSWER_RELATIVE).resolve()):
        raise ValueError(f"grader answer material cannot be evidence: {reference!r}")
    try:
        line_count = len(source.read_text(encoding="utf-8").splitlines())
    except UnicodeDecodeError as exc:
        raise ValueError(f"evidence source is not UTF-8 text: {reference!r}") from exc
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if start < 1 or end < start or end > line_count:
        raise ValueError(
            f"evidence line range is outside {relative}: {start}-{end} (lines={line_count})"
        )


def _evidence_reference_identity(reference: str, *, root: Path = ROOT) -> dict[str, Any]:
    """Freeze the exact bytes behind one SECRET evidence range."""

    _validate_evidence(reference, root=root)
    match = EVIDENCE_REFERENCE.fullmatch(reference)
    assert match is not None
    relative = Path(match.group("path"))
    source = (root / relative).resolve()
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    range_bytes = "".join(lines[start - 1 : end]).encode("utf-8")
    return {
        "reference": reference,
        "source_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "range_sha256": "sha256:" + hashlib.sha256(range_bytes).hexdigest(),
        "start_line": start,
        "end_line": end,
    }


def _expected_claims(qid: str, value: Any) -> tuple[ExpectedClaim, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{qid} SECRET expected_claims are required")
    claims: list[ExpectedClaim] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{qid} expected claim must be an object")
        claim_id = item.get("id")
        statement = item.get("statement")
        critical = item.get("critical")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValueError(f"{qid} expected claim id is required")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(f"{qid}/{claim_id} expected claim statement is required")
        if type(critical) is not bool:
            raise ValueError(f"{qid}/{claim_id} critical must be true or false")
        claims.append(ExpectedClaim(claim_id.strip(), statement.strip(), critical))
    if len({claim.id for claim in claims}) != len(claims):
        raise ValueError(f"{qid} expected claim IDs must be unique")
    return tuple(claims)


def _contradictions(qid: str, value: Any, severity: str) -> tuple[Contradiction, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{qid} SECRET contradictions must be a list")
    contradictions: list[Contradiction] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{qid} contradiction must be an object")
        contradiction_id = item.get("id")
        statement = item.get("statement")
        contradiction_severity = item.get("severity")
        misconception_id = item.get("misconception_id")
        if not isinstance(contradiction_id, str) or not contradiction_id.strip():
            raise ValueError(f"{qid} contradiction id is required")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(f"{qid}/{contradiction_id} contradiction statement is required")
        if contradiction_severity not in {"normal", "critical"}:
            raise ValueError(f"{qid}/{contradiction_id} contradiction severity is invalid")
        if misconception_id not in MISCONCEPTIONS:
            raise ValueError(f"{qid}/{contradiction_id} misconception_id is invalid")
        contradictions.append(
            Contradiction(
                contradiction_id.strip(),
                statement.strip(),
                contradiction_severity,
                misconception_id,
            )
        )
    if severity == "critical" and not contradictions:
        raise ValueError(f"critical question {qid} must declare at least one contradiction rule")
    if len({item.id for item in contradictions}) != len(contradictions):
        raise ValueError(f"{qid} contradiction IDs must be unique")
    return tuple(contradictions)


@lru_cache(maxsize=1)
def load_exam() -> tuple[ExamQuestion, ...]:
    prompts = _question_prompts()
    metadata = _metadata()
    secret_by_id: dict[
        str, tuple[str, str, tuple[ExpectedClaim, ...], tuple[Contradiction, ...], tuple[str, ...]]
    ] = {}
    for path, payload in _answer_payloads():
        if payload.get("schema_version") != SECRET_SCHEMA_VERSION:
            raise ValueError(f"SECRET schema_version mismatch in {path.relative_to(ROOT)}")
        if payload.get("source_version") != _source_version():
            raise ValueError(f"SECRET source_version mismatch in {path.relative_to(ROOT)}")
        section = payload.get("section")
        if not isinstance(section, str) or not section.strip():
            raise ValueError(f"SECRET section is missing: {path.relative_to(ROOT)}")
        entries = payload.get("questions")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"SECRET questions are missing: {path.relative_to(ROOT)}")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"invalid SECRET entry in {path.relative_to(ROOT)}")
            qid = entry.get("id")
            severity = entry.get("severity")
            if not isinstance(qid, str) or re.fullmatch(r"Q\d{2,3}", qid) is None:
                raise ValueError(f"invalid SECRET question id: {qid!r}")
            if qid in secret_by_id:
                raise ValueError(f"duplicate SECRET question id: {qid}")
            if severity not in {"normal", "critical"}:
                raise ValueError(f"invalid severity for {qid}: {severity!r}")
            claims = _expected_claims(qid, entry.get("expected_claims"))
            contradictions = _contradictions(qid, entry.get("contradictions"), severity)
            evidence = entry.get("evidence_expectations")
            if (
                not isinstance(evidence, list)
                or not evidence
                or not all(isinstance(ref, str) for ref in evidence)
            ):
                raise ValueError(f"{qid} must declare SECRET evidence_expectations")
            for reference in evidence:
                _validate_evidence(reference)
            secret_by_id[qid] = (section, severity, claims, contradictions, tuple(evidence))

    prompt_ids = set(prompts)
    if prompt_ids != set(metadata) or prompt_ids != set(secret_by_id):
        raise ValueError("PUBLIC prompts, PUBLIC metadata, and SECRET question IDs must match")
    ids = sorted(prompt_ids, key=lambda qid: int(qid[1:]))
    expected_ids = [f"Q{index:02d}" for index in range(1, len(ids) + 1)]
    if ids != expected_ids:
        raise ValueError("question IDs must be one contiguous Q01..QNN sequence")

    questions: list[ExamQuestion] = []
    for qid in ids:
        section, severity, claims, contradictions, evidence = secret_by_id[qid]
        public = metadata[qid]
        questions.append(
            ExamQuestion(
                id=qid,
                section=section,
                severity=severity,
                prompt=prompts[qid],
                type=public["type"],
                dimension=public["dimension"],
                difficulty=public["difficulty"],
                response_expectations=dict(public["response_expectations"]),
                evidence_scope=tuple(public["evidence_scope"]),
                blueprint_dimension=public["blueprint_dimension"],
                blueprint_form=public["blueprint_form"],
                expected_claims=claims,
                contradictions=contradictions,
                evidence_expectations=evidence,
            )
        )
    return tuple(questions)


@lru_cache(maxsize=1)
def _benchmark_registry_cached() -> dict[str, Any]:
    questions = load_exam()
    lifecycle = _question_lifecycle_cached()
    qualification_questions = _qualification_bank_questions(questions, lifecycle)
    return load_benchmark_registry(
        ROOT / BENCHMARK_REGISTRY_RELATIVE,
        source_version=_source_version(),
        questions=qualification_questions,
    )


def benchmark_registry() -> dict[str, Any]:
    return copy.deepcopy(_benchmark_registry_cached())


@lru_cache(maxsize=1)
def _question_audit_cached() -> dict[str, Any]:
    questions = load_exam()
    return load_question_audit(
        ROOT / QUESTION_AUDIT_RELATIVE,
        source_version=_source_version(),
        question_ids=(question.id for question in questions),
        current_question_bindings={
            question.id: _calibration_question_binding(question) for question in questions
        },
    )


def question_audit() -> dict[str, Any]:
    return copy.deepcopy(_question_audit_cached())


@lru_cache(maxsize=1)
def _question_lifecycle_cached() -> dict[str, Any]:
    questions = load_exam()
    return load_question_lifecycle(
        ROOT / QUESTION_LIFECYCLE_RELATIVE,
        source_version=_source_version(),
        question_ids=(question.id for question in questions),
    )


def question_lifecycle() -> dict[str, Any]:
    return copy.deepcopy(_question_lifecycle_cached())


def _severity_encoded_misconception_ids(misconceptions: tuple[str, ...]) -> list[str]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _severity_encoded_misconception_ids as impl,
    )

    return impl(misconceptions)


def _misconception_coverage_audit(questions: tuple[ExamQuestion, ...]) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _misconception_coverage_audit as impl,
    )

    return impl(questions)


def _assert_misconception_coverage_release_ready(
    questions: tuple[ExamQuestion, ...],
) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _assert_misconception_coverage_release_ready as impl,
    )

    return impl(questions)


def _evidence_authority_audit(questions: tuple[ExamQuestion, ...]) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _evidence_authority_audit as impl,
    )

    return impl(questions)


def _external_answerability_audit(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _external_answerability_audit as impl,
    )

    return impl(questions, lifecycle)


def _assert_external_answerability_ready(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _assert_external_answerability_ready as impl,
    )

    return impl(questions, lifecycle)


def _question_health_maintenance_audit(
    questions: tuple[ExamQuestion, ...],
    lifecycle: dict[str, Any],
    audit: dict[str, Any],
    answerability: dict[str, Any],
) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _question_health_maintenance_audit as impl,
    )

    return impl(questions, lifecycle, audit, answerability)


def _assert_question_health_maintenance_ready(
    questions: tuple[ExamQuestion, ...],
    lifecycle: dict[str, Any],
    audit: dict[str, Any],
    answerability: dict[str, Any],
) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_audits import (
        _assert_question_health_maintenance_ready as impl,
    )

    return impl(questions, lifecycle, audit, answerability)


def _question_snapshot(
    question: ExamQuestion, audit_row: dict[str, Any], lifecycle_row: dict[str, Any]
) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_snapshot import (
        _question_snapshot as impl,
    )

    return impl(question, audit_row, lifecycle_row)


def _calibration_question_binding(question: ExamQuestion) -> str:
    from oh_goon.repository_tooling._repository_understanding_material_snapshot import (
        _calibration_question_binding as impl,
    )

    return impl(question)


def _repository_evidence_index() -> dict[str, dict[str, Any]]:
    from oh_goon.repository_tooling._repository_understanding_material_snapshot import (
        _repository_evidence_index as impl,
    )

    return impl()


def _qualification_bank_questions(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> tuple[ExamQuestion, ...]:
    from oh_goon.repository_tooling._repository_understanding_material_snapshot import (
        _qualification_bank_questions as impl,
    )

    return impl(questions, lifecycle)


def _dimension_normalization_bank_audit(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> dict[str, Any]:
    from oh_goon.repository_tooling._repository_understanding_material_snapshot import (
        _dimension_normalization_bank_audit as impl,
    )

    return impl(questions, lifecycle)


@lru_cache(maxsize=1)
def _exam_snapshot_cached() -> dict[str, Any]:
    questions = load_exam()
    lifecycle = _question_lifecycle_cached()
    qualification_questions = _qualification_bank_questions(questions, lifecycle)
    blueprint_audit = assert_exam_release_ready(qualification_questions)
    misconception_audit = _assert_misconception_coverage_release_ready(qualification_questions)
    audit = _question_audit_cached()
    critical_question_ids = tuple(
        question.id for question in qualification_questions if question.severity == "critical"
    )
    audit_report = assert_question_audit_release_ready(
        audit, critical_question_ids=critical_question_ids
    )
    lifecycle_report = assert_current_score_bank_active(
        lifecycle, critical_question_ids=critical_question_ids
    )
    answerability_report = _assert_external_answerability_ready(questions, lifecycle)
    health_maintenance_report = _assert_question_health_maintenance_ready(
        questions, lifecycle, audit, answerability_report
    )
    dimension_normalization_audit = _dimension_normalization_bank_audit(questions, lifecycle)
    registry = _benchmark_registry_cached()
    integrity = benchmark_integrity_manifest(registry)
    audit_by_id = {row["id"]: row for row in audit["questions"]}
    lifecycle_by_id = {row["id"]: row for row in lifecycle["questions"]}
    base = {
        "qa_schema_version": QA_SCHEMA_VERSION,
        "question_schema_version": QUESTION_SCHEMA_VERSION,
        "secret_schema_version": SECRET_SCHEMA_VERSION,
        "grader_schema_version": GRADER_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "source_version": _source_version(),
        "repository_identity": canonical_source_identity(ROOT),
        "questions": [
            _question_snapshot(question, audit_by_id[question.id], lifecycle_by_id[question.id])
            for question in qualification_questions
        ],
        "blueprint_audit": blueprint_audit,
        "misconception_coverage_audit": misconception_audit,
        "evidence_authority_audit": _evidence_authority_audit(qualification_questions),
        "question_audit": audit,
        "question_audit_report": audit_report,
        "question_lifecycle": lifecycle,
        "question_lifecycle_report": lifecycle_report,
        "question_lifecycle_policy_version": QUESTION_LIFECYCLE_POLICY_VERSION,
        "external_answerability_policy_version": ANSWERABILITY_POLICY_VERSION,
        "external_answerability": answerability_report,
        "question_health_maintenance_policy_version": QUESTION_HEALTH_MAINTENANCE_POLICY_VERSION,
        "question_health_maintenance": health_maintenance_report,
        "dimension_normalization_policy_version": DIMENSION_NORMALIZATION_POLICY_VERSION,
        "dimension_normalization": dimension_normalization_audit,
        "human_baseline_policy_version": HUMAN_BASELINE_POLICY_VERSION,
        "calibration_policy_version": CALIBRATION_POLICY_VERSION,
        "calibration_promotion_review_policy_version": CALIBRATION_PROMOTION_REVIEW_POLICY_VERSION,
        "stability_policy_version": STABILITY_POLICY_VERSION,
        "capability_policy_version": CAPABILITY_POLICY_VERSION,
        "differential_recertification_policy_version": DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION,
        "game_tape_policy_version": GAME_TAPE_POLICY_VERSION,
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "benchmark_integrity_policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "benchmark_integrity": integrity,
        "benchmark_registry": registry,
        "relationship_graph": graph_spec(),
        "repository_evidence_index": _repository_evidence_index(),
    }
    return {**base, "snapshot_sha256": _canonical_json_sha256(base)}


@lru_cache(maxsize=1)
def _qualification_blueprint_audit_cached() -> dict[str, Any]:
    questions = load_exam()
    lifecycle = _question_lifecycle_cached()
    return assert_exam_release_ready(_qualification_bank_questions(questions, lifecycle))


def qualification_blueprint_audit() -> dict[str, Any]:
    """Return a defensive copy of the process-local qualification blueprint audit."""

    return copy.deepcopy(_qualification_blueprint_audit_cached())


def exam_snapshot() -> dict[str, Any]:
    """Return a defensive copy of one process-local immutable source snapshot."""

    return copy.deepcopy(_exam_snapshot_cached())


@lru_cache(maxsize=1)
def _manifest_cached() -> dict[str, Any]:
    _validate_source_symlinks()
    questions = load_exam()
    lifecycle = _question_lifecycle_cached()
    qualification_questions = _qualification_bank_questions(questions, lifecycle)
    blueprint_audit = assert_exam_release_ready(qualification_questions)
    misconception_audit = _assert_misconception_coverage_release_ready(qualification_questions)
    audit = _question_audit_cached()
    critical_question_ids = tuple(
        question.id for question in qualification_questions if question.severity == "critical"
    )
    audit_report = assert_question_audit_release_ready(
        audit, critical_question_ids=critical_question_ids
    )
    lifecycle_report = assert_current_score_bank_active(
        lifecycle, critical_question_ids=critical_question_ids
    )
    answerability_report = _assert_external_answerability_ready(questions, lifecycle)
    health_maintenance_report = _assert_question_health_maintenance_ready(
        questions, lifecycle, audit, answerability_report
    )
    dimension_normalization_audit = _dimension_normalization_bank_audit(questions, lifecycle)
    registry = _benchmark_registry_cached()
    integrity = benchmark_integrity_manifest(registry)
    question_body = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / QUESTION_RELATIVE).glob("*.md"))
    )
    for forbidden in (CANDIDATE_GOLD_MARKER, "why_it_matters", "expected_claims"):
        if forbidden in question_body:
            raise ValueError(f"blind question material leaks grader-only marker: {forbidden}")
    if INLINE_SOURCE_REFERENCE.search(question_body):
        raise ValueError("blind question material contains a source line reference")

    type_counts = {name: 0 for name in QUESTION_TYPES}
    difficulty_counts = {name: 0 for name in DIFFICULTIES}
    dimension_counts = {name: 0 for name in DIMENSIONS}
    for question in qualification_questions:
        type_counts[question.type] += 1
        difficulty_counts[question.difficulty] += 1
        dimension_counts[question.dimension] += 1

    base = {
        "source_version": _source_version(),
        "qa_schema_version": QA_SCHEMA_VERSION,
        "question_schema_version": QUESTION_SCHEMA_VERSION,
        "secret_schema_version": SECRET_SCHEMA_VERSION,
        "grader_schema_version": GRADER_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
        "question_count": len(questions),
        "qualification_question_count": len(qualification_questions),
        "shadow_question_count": len(questions) - len(qualification_questions),
        "critical_question_count": sum(
            question.severity == "critical" for question in qualification_questions
        ),
        "critical_claim_count": sum(
            claim.critical
            for question in qualification_questions
            for claim in question.expected_claims
        ),
        "question_types": type_counts,
        "difficulty": difficulty_counts,
        "dimensions": dimension_counts,
        "blueprint_audit": blueprint_audit,
        "misconception_coverage_audit": misconception_audit,
        "evidence_authority_audit": _evidence_authority_audit(qualification_questions),
        "question_audit": audit_report,
        "question_audit_policy_version": QUESTION_AUDIT_POLICY_VERSION,
        "question_lifecycle": lifecycle_report,
        "question_lifecycle_policy_version": QUESTION_LIFECYCLE_POLICY_VERSION,
        "external_answerability_policy_version": ANSWERABILITY_POLICY_VERSION,
        "external_answerability": answerability_report,
        "question_health_maintenance_policy_version": QUESTION_HEALTH_MAINTENANCE_POLICY_VERSION,
        "question_health_maintenance": health_maintenance_report,
        "dimension_normalization_policy_version": DIMENSION_NORMALIZATION_POLICY_VERSION,
        "dimension_normalization": dimension_normalization_audit,
        "human_baseline_policy_version": HUMAN_BASELINE_POLICY_VERSION,
        "calibration_policy_version": CALIBRATION_POLICY_VERSION,
        "calibration_promotion_review_policy_version": CALIBRATION_PROMOTION_REVIEW_POLICY_VERSION,
        "stability_policy": stability_policy_manifest(),
        "capability_policy": capability_policy_manifest(),
        "differential_recertification_policy": recertification_policy_manifest(),
        "game_tape_policy": game_tape_policy_manifest(),
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "benchmark_integrity": integrity,
        "claim_states": list(CLAIM_STATES),
        "candidate_confidence_states": list(CANDIDATE_CONFIDENCE_STATES),
        "candidate_epistemic_states": list(CANDIDATE_EPISTEMIC_STATES),
        "question_states": list(QUESTION_STATES),
        "reference_states": list(REFERENCE_STATES),
        "assertion_relations": list(ASSERTION_RELATIONS),
        "qualification": qualification_policy_manifest(),
    }
    return {**base, "manifest_sha256": _canonical_json_sha256(base)}


def manifest() -> dict[str, Any]:
    """Return a defensive copy of cached immutable manifest material."""

    return copy.deepcopy(_manifest_cached())


def verify(*, manifest_path: Path | None = None) -> dict[str, Any]:
    payload = manifest()
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def prepare(destination: Path, *, force: bool = False) -> Path:
    from oh_goon.repository_tooling._repository_understanding_material_prepare import prepare_exam

    return prepare_exam(destination, force=force)
