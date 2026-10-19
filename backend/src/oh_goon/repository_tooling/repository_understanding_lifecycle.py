"""SECRET question lifecycle, human-baseline, and promotion authority.

Lifecycle state is bank governance, not candidate grading. Empirical calibration and human baseline
reports provide evidence to an explicit transition review; they never promote, retire, reweight, or
change a question automatically.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml

QUESTION_LIFECYCLE_POLICY_VERSION = "repository-question-lifecycle.v3"
HUMAN_BASELINE_POLICY_VERSION = "repository-understanding-human-baseline.v1"
QUESTION_LIFECYCLE_SCHEMA_VERSION = 3
QUESTION_TRANSITION_SCHEMA_VERSION = 1
HUMAN_BASELINE_OBSERVATION_SCHEMA_VERSION = 1
HUMAN_BASELINE_REPORT_SCHEMA_VERSION = 1

QUESTION_LIFECYCLE_STATES = (
    "DRAFT",
    "SHADOW",
    "CALIBRATING",
    "ACTIVE",
    "REVIEW_REQUIRED",
    "QUARANTINED",
    "RETIRED",
)
LEGAL_TRANSITIONS = {
    ("DRAFT", "SHADOW"),
    ("SHADOW", "CALIBRATING"),
    ("CALIBRATING", "ACTIVE"),
    ("ACTIVE", "REVIEW_REQUIRED"),
    ("ACTIVE", "RETIRED"),
    ("REVIEW_REQUIRED", "ACTIVE"),
    ("REVIEW_REQUIRED", "QUARANTINED"),
    ("REVIEW_REQUIRED", "RETIRED"),
    ("QUARANTINED", "REVIEW_REQUIRED"),
    ("QUARANTINED", "RETIRED"),
}
HUMAN_REFERENCE_ASSESSMENTS = ("AGREE", "SUSPECT", "AMBIGUOUS")
HUMAN_OUTCOMES = ("SUCCESS", "PARTIAL", "INCORRECT", "ABSTAINED")
_SHA256_IDENTITY = re.compile(r"sha256:[0-9a-f]{64}")
_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(body).hexdigest()


def _identity(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_IDENTITY.fullmatch(value) is None:
        raise ValueError(f"{label} must be a sha256 identity")
    return value


def _assert_embedded_identity(payload: dict[str, Any], *, field: str, label: str) -> str:
    identity = _identity(payload.get(field), label=field)
    expected = "sha256:" + _canonical_sha256(
        {key: value for key, value in payload.items() if key != field}
    )
    if identity != expected:
        raise ValueError(f"{label} identity does not match artifact bytes")
    return identity


def _version_not_future(
    value: Any, *, label: str, source_version: str, allow_legacy: bool = False
) -> str:
    if allow_legacy and value == "PRE_LIFECYCLE":
        return value
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical numeric version")
    current = tuple(int(part) for part in source_version.split("."))
    observed = tuple(int(part) for part in value.split("."))
    if observed > current:
        raise ValueError(f"{label} cannot be in the future")
    return value


def _state_projection(state: str) -> dict[str, Any]:
    return {
        "score_bearing": state == "ACTIVE",
        "candidate_delivery": state in {"SHADOW", "CALIBRATING", "ACTIVE"},
        "delivery_mode": {
            "DRAFT": "AUTHOR_ONLY",
            "SHADOW": "SHADOW_NON_SCORE_BEARING",
            "CALIBRATING": "CALIBRATION_NON_SCORE_BEARING",
            "ACTIVE": "SCORE_BEARING",
            "REVIEW_REQUIRED": "WITHHELD_REVIEW_REQUIRED",
            "QUARANTINED": "WITHHELD_QUARANTINED",
            "RETIRED": "WITHHELD",
        }[state],
    }


def load_question_lifecycle(
    path: Path, *, source_version: str, question_ids: Iterable[str]
) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("repository question lifecycle must be an object")
    if payload.get("schema_version") != QUESTION_LIFECYCLE_SCHEMA_VERSION:
        raise ValueError("repository question lifecycle schema mismatch")
    if payload.get("policy_version") != QUESTION_LIFECYCLE_POLICY_VERSION:
        raise ValueError("repository question lifecycle policy mismatch")
    if payload.get("source_version") != source_version:
        raise ValueError("repository question lifecycle source_version mismatch")
    rows = payload.get("questions")
    if not isinstance(rows, list):
        raise ValueError("repository question lifecycle questions must be a list")
    expected = tuple(question_ids)
    observed: list[str] = []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("repository question lifecycle row must be an object")
        qid = row.get("id")
        state = row.get("state")
        if not isinstance(qid, str):
            raise ValueError("repository question lifecycle row id is required")
        if state not in QUESTION_LIFECYCLE_STATES:
            raise ValueError(f"{qid} has invalid lifecycle state")
        introduced = _version_not_future(
            row.get("introduced_source_version"),
            label=f"{qid} introduced_source_version",
            source_version=source_version,
            allow_legacy=True,
        )
        state_since = _version_not_future(
            row.get("state_since_source_version"),
            label=f"{qid} state_since_source_version",
            source_version=source_version,
        )
        projection = _state_projection(state)
        for key, expected_value in projection.items():
            if row.get(key) != expected_value:
                raise ValueError(f"{qid} {key} does not match lifecycle state {state}")
        activation_basis = row.get("activation_basis")
        if state == "ACTIVE":
            if activation_basis not in {"LEGACY_ACTIVE", "PROMOTION_REVIEW"}:
                raise ValueError(f"{qid} ACTIVE requires a valid activation_basis")
        elif activation_basis is not None:
            raise ValueError(f"{qid} activation_basis is only valid for ACTIVE questions")
        qualification_bank_member = row.get("qualification_bank_member")
        if type(qualification_bank_member) is not bool:
            raise ValueError(f"{qid} qualification_bank_member must be boolean")
        if state == "ACTIVE" and not qualification_bank_member:
            raise ValueError(f"{qid} ACTIVE must be a qualification_bank_member")
        observed.append(qid)
        normalized.append(
            {
                "id": qid,
                "state": state,
                "introduced_source_version": introduced,
                "state_since_source_version": state_since,
                **projection,
                "qualification_bank_member": qualification_bank_member,
                "activation_basis": activation_basis,
            }
        )
    if tuple(observed) != expected:
        raise ValueError("repository question lifecycle IDs/order must match the canonical bank")
    base = {
        "schema_version": QUESTION_LIFECYCLE_SCHEMA_VERSION,
        "policy_version": QUESTION_LIFECYCLE_POLICY_VERSION,
        "source_version": source_version,
        "questions": normalized,
    }
    return {**base, "lifecycle_identity": "sha256:" + _canonical_sha256(base)}


def question_lifecycle_manifest(
    lifecycle: dict[str, Any], *, critical_question_ids: Iterable[str] = ()
) -> dict[str, Any]:
    states = Counter(row["state"] for row in lifecycle["questions"])
    score_bearing = [row["id"] for row in lifecycle["questions"] if row["score_bearing"]]
    qualification_bank = [row for row in lifecycle["questions"] if row["qualification_bank_member"]]
    shadow_pool = [
        row["id"] for row in lifecycle["questions"] if not row["qualification_bank_member"]
    ]
    critical_ids = set(critical_question_ids)
    unavailable = [row["id"] for row in qualification_bank if row["state"] != "ACTIVE"]
    unavailable_critical = [qid for qid in unavailable if qid in critical_ids]
    qualification_status = (
        "BLOCKED" if unavailable_critical else ("DEGRADED" if unavailable else "READY")
    )
    return {
        "policy_version": QUESTION_LIFECYCLE_POLICY_VERSION,
        "lifecycle_identity": lifecycle["lifecycle_identity"],
        "question_count": len(lifecycle["questions"]),
        "states": {state: states.get(state, 0) for state in QUESTION_LIFECYCLE_STATES},
        "qualification_bank_count": len(qualification_bank),
        "shadow_pool_count": len(shadow_pool),
        "shadow_pool_ids": shadow_pool,
        "score_bearing_count": len(score_bearing),
        "candidate_delivery_count": sum(
            row["candidate_delivery"] for row in lifecycle["questions"]
        ),
        "unavailable_question_count": len(unavailable),
        "unavailable_question_ids": unavailable,
        "unavailable_critical_question_count": len(unavailable_critical),
        "unavailable_critical_question_ids": unavailable_critical,
        "qualification_status": qualification_status,
        "rule": "only ACTIVE qualification-bank members are score-bearing; pre-promotion shadow items do not rescale the qualification bank; unavailable critical qualification coverage blocks qualification",
    }


def assert_current_score_bank_active(
    lifecycle: dict[str, Any], *, critical_question_ids: Iterable[str] = ()
) -> dict[str, Any]:
    """Current fixed exam material is the ACTIVE bank; non-ACTIVE authored items stay out of it.

    SHADOW/CALIBRATING experiments are delivered through the hidden challenge pool until exam
    assembly grows a separate non-score-bearing public-question surface.
    """

    manifest = question_lifecycle_manifest(lifecycle, critical_question_ids=critical_question_ids)
    inactive = manifest["unavailable_question_ids"]
    if inactive:
        critical = manifest["unavailable_critical_question_ids"]
        detail = (
            ""
            if not critical
            else "; critical qualification coverage blocked: " + ", ".join(critical)
        )
        raise ValueError(
            "current qualification bank contains non-ACTIVE questions; qualification thresholds are not rescaled: "
            + ", ".join(inactive)
            + detail
        )
    return manifest


def _median(values: list[float]) -> float | None:
    return None if not values else round(float(statistics.median(values)), 3)


def _rate(values: list[bool]) -> float | None:
    return None if not values else round(100.0 * sum(values) / len(values), 1)


def human_baseline_report(
    observations_path: Path,
    *,
    question_ids: Iterable[str],
    source_version: str,
    repository_identity: str,
    exam_snapshot_sha256: str,
) -> dict[str, Any]:
    """Compute expert timing/performance/disagreement telemetry without mutating bank policy."""

    payload = json.loads(observations_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("human baseline observations must be a JSON object")
    if payload.get("artifact_type") != "repository-understanding-human-baseline-observations":
        raise ValueError("human baseline observations artifact_type mismatch")
    if payload.get("schema_version") != HUMAN_BASELINE_OBSERVATION_SCHEMA_VERSION:
        raise ValueError("human baseline observations schema mismatch")
    expected_identity = {
        "source_version": source_version,
        "repository_identity": repository_identity,
        "exam_snapshot_sha256": exam_snapshot_sha256,
    }
    for field, expected in expected_identity.items():
        if payload.get(field) != expected:
            raise ValueError(f"human baseline observations {field} mismatch")
    reviewers = payload.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers:
        raise ValueError("human baseline observations require at least one reviewer")
    ids = tuple(question_ids)
    id_set = set(ids)
    rows_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reviewer_ids: set[str] = set()
    for reviewer in reviewers:
        if not isinstance(reviewer, dict):
            raise ValueError("human baseline reviewer must be an object")
        reviewer_id = reviewer.get("reviewer_id")
        if (
            not isinstance(reviewer_id, str)
            or not reviewer_id.strip()
            or reviewer_id in reviewer_ids
        ):
            raise ValueError("human baseline reviewer_id must be unique and non-empty")
        reviewer_ids.add(reviewer_id)
        expertise = reviewer.get("expertise")
        if not isinstance(expertise, str) or not expertise.strip():
            raise ValueError(f"human baseline reviewer {reviewer_id} expertise is required")
        answers = reviewer.get("questions")
        if not isinstance(answers, list):
            raise ValueError(f"human baseline reviewer {reviewer_id} questions must be a list")
        seen: set[str] = set()
        for answer in answers:
            if not isinstance(answer, dict):
                raise ValueError("human baseline question observation must be an object")
            qid = answer.get("id")
            if qid not in id_set or qid in seen:
                raise ValueError(f"human baseline contains unknown/duplicate question id: {qid}")
            outcome = answer.get("outcome")
            if outcome not in HUMAN_OUTCOMES:
                raise ValueError(f"{qid} has invalid human baseline outcome")
            reference = answer.get("reference_assessment")
            if reference not in HUMAN_REFERENCE_ASSESSMENTS:
                raise ValueError(f"{qid} has invalid reference_assessment")
            seconds = answer.get("time_to_evidence_seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
                raise ValueError(f"{qid} time_to_evidence_seconds must be non-negative")
            rows_by_question[qid].append(
                {
                    "reviewer_id": reviewer_id,
                    "expertise": expertise.strip(),
                    "outcome": outcome,
                    "reference_assessment": reference,
                    "time_to_evidence_seconds": float(seconds),
                }
            )
            seen.add(qid)

    question_reports: dict[str, Any] = {}
    disagreement_questions: list[str] = []
    for qid in ids:
        rows = rows_by_question.get(qid, [])
        reference_counts = Counter(row["reference_assessment"] for row in rows)
        outcomes = Counter(row["outcome"] for row in rows)
        assessments = {row["reference_assessment"] for row in rows}
        reference_disagreement = len(assessments) > 1 or any(
            row["reference_assessment"] != "AGREE" for row in rows
        )
        if reference_disagreement:
            disagreement_questions.append(qid)
        question_reports[qid] = {
            "reviewer_count": len(rows),
            "median_time_to_evidence_seconds": _median(
                [row["time_to_evidence_seconds"] for row in rows]
            ),
            "success_rate_percent": _rate([row["outcome"] == "SUCCESS" for row in rows]),
            "outcome_distribution": dict(sorted(outcomes.items())),
            "reference_assessment_distribution": dict(sorted(reference_counts.items())),
            "reference_disagreement": reference_disagreement,
            "reference_agreement_percent": _rate(
                [row["reference_assessment"] == "AGREE" for row in rows]
            ),
        }
    base = {
        "artifact_type": "repository-understanding-human-baseline-report",
        "schema_version": HUMAN_BASELINE_REPORT_SCHEMA_VERSION,
        "policy_version": HUMAN_BASELINE_POLICY_VERSION,
        **expected_identity,
        "reviewer_count": len(reviewer_ids),
        "question_observation_count": sum(len(rows) for rows in rows_by_question.values()),
        "questions": question_reports,
        "reference_disagreement_questions": disagreement_questions,
        "maturity": "MULTI_REVIEWER" if len(reviewer_ids) >= 2 else "SINGLE_REVIEWER",
        "authority_note": (
            "human timing, success and disagreement are calibration evidence only; they cannot automatically change difficulty, scoring, lifecycle, or qualification"
        ),
    }
    return {**base, "human_baseline_identity": "sha256:" + _canonical_sha256(base)}


def authorize_question_transition(
    request_path: Path,
    *,
    lifecycle: dict[str, Any],
    question_audit: dict[str, Any],
    calibration_report: dict[str, Any] | None = None,
    human_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one explicit bank-authority transition; never mutates source material itself."""

    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("question lifecycle transition request must be a JSON object")
    if request.get("artifact_type") != "repository-question-lifecycle-transition-request":
        raise ValueError("question lifecycle transition request artifact_type mismatch")
    if request.get("schema_version") != QUESTION_TRANSITION_SCHEMA_VERSION:
        raise ValueError("question lifecycle transition request schema mismatch")
    if request.get("policy_version") != QUESTION_LIFECYCLE_POLICY_VERSION:
        raise ValueError("question lifecycle transition request policy mismatch")
    lifecycle_identity = _assert_embedded_identity(
        lifecycle, field="lifecycle_identity", label="question lifecycle"
    )
    if request.get("source_version") != lifecycle["source_version"]:
        raise ValueError("question lifecycle transition source_version mismatch")
    if request.get("lifecycle_identity") != lifecycle_identity:
        raise ValueError("question lifecycle transition lifecycle_identity mismatch")

    by_id = {row["id"]: row for row in lifecycle["questions"]}
    audit_by_id = {row["id"]: row for row in question_audit["questions"]}
    qid = request.get("question_id")
    if qid not in by_id:
        raise ValueError(f"question lifecycle transition references unknown question: {qid}")
    current = by_id[qid]
    from_state = request.get("from_state")
    to_state = request.get("to_state")
    if from_state != current["state"]:
        raise ValueError("question lifecycle transition from_state does not match current bank")
    if (from_state, to_state) not in LEGAL_TRANSITIONS:
        raise ValueError(f"illegal question lifecycle transition: {from_state} -> {to_state}")

    evidence: dict[str, Any] = {}
    if (from_state, to_state) == ("DRAFT", "SHADOW"):
        evidence["author_review_identity"] = _identity(
            request.get("author_review_identity"), label="author_review_identity"
        )
    elif (from_state, to_state) == ("SHADOW", "CALIBRATING"):
        evidence["author_review_identity"] = _identity(
            request.get("author_review_identity"), label="author_review_identity"
        )
        evidence["shadow_observation_identity"] = _identity(
            request.get("shadow_observation_identity"), label="shadow_observation_identity"
        )
    elif (from_state, to_state) == ("CALIBRATING", "ACTIVE"):
        if audit_by_id[qid]["health_status"] != "VALID":
            raise ValueError(f"{qid} cannot become ACTIVE while question health is not VALID")
        if calibration_report is None or human_baseline is None:
            raise ValueError(
                "CALIBRATING -> ACTIVE requires calibration and human baseline reports"
            )
        _assert_embedded_identity(
            calibration_report, field="report_identity", label="calibration report"
        )
        _assert_embedded_identity(
            human_baseline, field="human_baseline_identity", label="human baseline report"
        )
        if calibration_report.get("source_version") != lifecycle["source_version"]:
            raise ValueError("calibration report source_version does not match lifecycle")
        if human_baseline.get("source_version") != lifecycle["source_version"]:
            raise ValueError("human baseline source_version does not match lifecycle")
        requested_calibration = _identity(
            request.get("calibration_report_identity"), label="calibration_report_identity"
        )
        if calibration_report.get("report_identity") != requested_calibration:
            raise ValueError("calibration report identity does not match transition request")
        requested_human = _identity(
            request.get("human_baseline_identity"), label="human_baseline_identity"
        )
        if human_baseline.get("human_baseline_identity") != requested_human:
            raise ValueError("human baseline identity does not match transition request")
        calibration_row = (calibration_report.get("questions") or {}).get(qid)
        human_row = (human_baseline.get("questions") or {}).get(qid)
        if (
            not isinstance(calibration_row, dict)
            or calibration_row.get("observation_count", 0) <= 0
        ):
            raise ValueError(f"{qid} has no empirical calibration observations")
        if not isinstance(human_row, dict) or human_row.get("reviewer_count", 0) < 2:
            raise ValueError(
                f"{qid} ACTIVE promotion requires at least two independent human reviewers"
            )
        if human_row.get("reference_disagreement"):
            evidence["adjudication_identity"] = _identity(
                request.get("adjudication_identity"), label="adjudication_identity"
            )
        evidence.update(
            {
                "calibration_report_identity": requested_calibration,
                "human_baseline_identity": requested_human,
                "promotion_review_identity": _identity(
                    request.get("promotion_review_identity"), label="promotion_review_identity"
                ),
            }
        )
    elif (from_state, to_state) == ("ACTIVE", "RETIRED"):
        evidence["retirement_review_identity"] = _identity(
            request.get("retirement_review_identity"), label="retirement_review_identity"
        )
        reason = request.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("ACTIVE -> RETIRED requires a non-empty reason")
        evidence["reason"] = reason.strip()

    next_projection = _state_projection(to_state)
    base = {
        "artifact_type": "repository-question-lifecycle-transition-authorization",
        "schema_version": QUESTION_TRANSITION_SCHEMA_VERSION,
        "policy_version": QUESTION_LIFECYCLE_POLICY_VERSION,
        "source_version": lifecycle["source_version"],
        "lifecycle_identity": lifecycle["lifecycle_identity"],
        "question_id": qid,
        "from_state": from_state,
        "to_state": to_state,
        "authorized": True,
        "next_state_projection": next_projection,
        "evidence": evidence,
        "authority_note": (
            "authorization is evidence for the SECRET bank owner; it does not mutate source, scores, or qualification automatically"
        ),
    }
    return {**base, "transition_identity": "sha256:" + _canonical_sha256(base)}
