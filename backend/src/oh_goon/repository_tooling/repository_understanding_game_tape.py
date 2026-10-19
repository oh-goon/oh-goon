"""Read-only Game Tape projection for Repository Understanding certification evidence.

The Game Tape is an explainability artifact, never a scoring, lifecycle, certificate, or
runtime permission authority. It revalidates the authoritative inputs and projects their
existing decisions into investigation-oriented views without inventing new thresholds.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from oh_goon.repository_tooling.repository_understanding_capability import (
    CAPABILITY_POLICY_VERSION,
    capability_certificate,
    validate_capability_certificate,
)
from oh_goon.repository_tooling.repository_understanding_recertification import (
    DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION,
    validate_recertification_plan,
)
from oh_goon.repository_tooling.repository_understanding_stability import (
    STABILITY_POLICY_VERSION,
    stability_report,
)

GAME_TAPE_POLICY_VERSION = "repository-understanding-game-tape.v1"
GAME_TAPE_SCHEMA_VERSION = 1
GAME_TAPE_TABS = (
    "overview",
    "concepts",
    "questions",
    "evidence",
    "consistency_epistemics",
    "stability",
    "benchmark_integrity",
    "qualification_capability",
    "recertification",
)
_AUTHORITY_CONTRACT = {
    "projection_only": True,
    "score_authority": "repository-understanding result / scoring policy",
    "lifecycle_authority": "repository-question-lifecycle.v1",
    "stability_authority": STABILITY_POLICY_VERSION,
    "capability_authority": CAPABILITY_POLICY_VERSION,
    "recertification_authority": DIFFERENTIAL_RECERTIFICATION_POLICY_VERSION,
    "runtime_permission_authority": "external execution/admission owner",
    "may_change_score": False,
    "may_change_question_lifecycle": False,
    "may_grant_capability": False,
    "may_reauthorize_stale_certificate": False,
}


def _canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _result_by_run(paths: list[Path], stability: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = _load_json(path, label="repository-understanding result")
        run_id = payload.get("candidate_run_identity")
        if not isinstance(run_id, str) or run_id in rows:
            raise ValueError("Game Tape requires one unique scored result per candidate run")
        rows[run_id] = payload
    expected = list(stability.get("candidate_run_identities") or [])
    if set(rows) != set(expected):
        raise ValueError("Game Tape scored-result run identities do not match stability evidence")
    return rows


def _observation_references(result: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    bundle = result.get("regrade_bundle")
    if not isinstance(bundle, dict):
        return {}
    observations = bundle.get("grader_observations")
    if not isinstance(observations, list):
        return {}
    references: dict[tuple[str, str], list[str]] = {}
    for question in observations:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str):
            continue
        qid = str(question["id"])
        claims = question.get("claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("claim_id"), str):
                continue
            evidence = claim.get("evidence")
            refs = evidence.get("references") if isinstance(evidence, dict) else None
            if isinstance(refs, list):
                references[(qid, str(claim["claim_id"]))] = [
                    str(ref) for ref in refs if isinstance(ref, str)
                ]
    return references


def _question_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    questions = result.get("questions")
    if not isinstance(questions, list):
        raise ValueError("Game Tape result is missing question results")
    rows: dict[str, dict[str, Any]] = {}
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str):
            raise ValueError("Game Tape result contains invalid question rows")
        qid = str(question["id"])
        if qid in rows:
            raise ValueError(f"Game Tape result contains duplicate question: {qid}")
        rows[qid] = question
    return rows


def _concept_projection(
    run_ids: list[str], results: dict[str, dict[str, Any]], stability: dict[str, Any]
) -> list[dict[str, Any]]:
    per_run = {run: _question_map(results[run]) for run in run_ids}
    concepts: dict[str, dict[str, Any]] = {}
    for qid, question in per_run[run_ids[0]].items():
        if not question.get("included"):
            continue
        concept = str(question.get("concept_family") or "")
        if not concept:
            raise ValueError(f"Game Tape question {qid} is missing concept_family")
        row = concepts.setdefault(
            concept,
            {"concept_family": concept, "question_ids": [], "critical": False},
        )
        row["question_ids"].append(qid)
        claims = question.get("claims") or []
        row["critical"] = bool(
            row["critical"]
            or question.get("severity") == "critical"
            or any(bool(claim.get("critical")) for claim in claims if isinstance(claim, dict))
        )
    unstable = {
        str(item.get("concept_family"))
        for item in stability.get("critical_concept_oscillations", [])
        if isinstance(item, dict)
    }
    projected: list[dict[str, Any]] = []
    for concept in sorted(concepts):
        row = concepts[concept]
        run_summaries: list[dict[str, Any]] = []
        for run in run_ids:
            questions = [per_run[run][qid] for qid in row["question_ids"]]
            included = [question for question in questions if question.get("included")]
            scores = [float(question.get("score", 0.0)) for question in included]
            evidence = [float(question.get("evidence_quality", 0.0)) for question in included]
            run_summaries.append(
                {
                    "candidate_run_identity": run,
                    "question_count": len(included),
                    "minimum_question_score": min(scores) if scores else None,
                    "maximum_question_score": max(scores) if scores else None,
                    "minimum_evidence_quality": min(evidence) if evidence else None,
                    "critical_failures": sorted(
                        question["id"] for question in included if question.get("critical_failed")
                    ),
                }
            )
        projected.append(
            {
                **row,
                "stable_across_qualifying_runs": concept not in unstable,
                "run_summaries": run_summaries,
            }
        )
    return projected


def _question_projection(
    run_ids: list[str], results: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    per_run = {run: _question_map(results[run]) for run in run_ids}
    first = per_run[run_ids[0]]
    rows: list[dict[str, Any]] = []
    for qid in first:
        canonical = first[qid]
        runs = []
        for run in run_ids:
            question = per_run[run].get(qid)
            if question is None:
                raise ValueError(f"Game Tape run is missing question {qid}")
            runs.append(
                {
                    "candidate_run_identity": run,
                    "included": bool(question.get("included")),
                    "question_status": question.get("question_status"),
                    "reference_status": question.get("reference_status"),
                    "score": question.get("score"),
                    "evidence_quality": question.get("evidence_quality"),
                    "critical_failed": bool(question.get("critical_failed")),
                    "observed_contradictions": list(question.get("observed_contradictions") or []),
                    "claim_states": [
                        {
                            "claim_id": claim.get("claim_id"),
                            "result": claim.get("result"),
                            "critical": bool(claim.get("critical")),
                            "critical_failed": bool(claim.get("critical_failed")),
                            "candidate_epistemic_state": claim.get("candidate_epistemic_state"),
                            "candidate_confidence": claim.get("candidate_confidence"),
                        }
                        for claim in question.get("claims", [])
                        if isinstance(claim, dict)
                    ],
                }
            )
        rows.append(
            {
                "id": qid,
                "dimension": canonical.get("dimension"),
                "concept_family": canonical.get("concept_family"),
                "severity": canonical.get("severity"),
                "runs": runs,
            }
        )
    return rows


def _evidence_projection(
    run_ids: list[str], results: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in run_ids:
        result = results[run]
        refs = _observation_references(result)
        for question in result.get("questions", []):
            if not isinstance(question, dict) or not question.get("included"):
                continue
            qid = str(question.get("id"))
            for claim in question.get("claims", []):
                if not isinstance(claim, dict):
                    continue
                claim_id = str(claim.get("claim_id"))
                rows.append(
                    {
                        "candidate_run_identity": run,
                        "question_id": qid,
                        "claim_id": claim_id,
                        "result": claim.get("result"),
                        "critical": bool(claim.get("critical")),
                        "evidence_quality": claim.get("evidence_quality"),
                        "minimum_evidence_authority": claim.get("minimum_evidence_authority"),
                        "evidence_authority_floor_met": claim.get("evidence_authority_floor_met"),
                        "strongest_evidence_authority": claim.get("strongest_evidence_authority"),
                        "evidence_authority_classes": list(
                            claim.get("evidence_authority_classes") or []
                        ),
                        "independent_evidence_authority_classes": claim.get(
                            "independent_evidence_authority_classes"
                        ),
                        "candidate_references": refs.get((qid, claim_id), []),
                    }
                )
    return rows


def _consistency_projection(
    run_ids: list[str], results: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for run in run_ids:
        result = results[run]
        calibration = result.get("confidence_calibration") or {}
        rows.append(
            {
                "candidate_run_identity": run,
                "mental_model_consistency": result.get("mental_model_consistency"),
                "mental_model_assertion_count": result.get("mental_model_assertion_count"),
                "cross_answer_findings": list(result.get("cross_answer_findings") or []),
                "relationship_graph": result.get("relationship_graph"),
                "misconception_findings": list(result.get("misconception_findings") or []),
                "unsupported_assertions": result.get("unsupported_assertions"),
                "contradicted_claims": result.get("contradicted_claims"),
                "fabricated_evidence": list(result.get("fabricated_evidence") or []),
                "critical_failures": list(result.get("critical_failures") or []),
                "adjudication_required": list(result.get("adjudication_required") or []),
                "candidate_epistemic": calibration.get("candidate_epistemic"),
            }
        )
    return rows


def _benchmark_projection(
    run_ids: list[str],
    results: dict[str, dict[str, Any]],
    integrity_paths: list[Path],
    adversarial_paths: list[Path],
) -> list[dict[str, Any]]:
    integrity = [_load_json(path, label="benchmark integrity result") for path in integrity_paths]
    adversarial = [_load_json(path, label="adversarial result") for path in adversarial_paths]
    integrity_by_run = {str(row.get("candidate_run_identity")): row for row in integrity}
    adversarial_by_challenge = {
        str(row.get("challenge_instance_identity")): row for row in adversarial
    }
    rows: list[dict[str, Any]] = []
    for run in run_ids:
        if run not in integrity_by_run:
            raise ValueError(f"Game Tape benchmark evidence is missing run {run}")
        irow = integrity_by_run[run]
        challenge = str(irow.get("challenge_instance_identity"))
        arow = adversarial_by_challenge.get(challenge)
        if arow is None:
            raise ValueError(f"Game Tape adversarial evidence is missing challenge for run {run}")
        rows.append(
            {
                "candidate_run_identity": run,
                "run_certificate_identity": results[run].get("certificate_identity"),
                "integrity_result_identity": irow.get("integrity_result_identity"),
                "challenge_instance_identity": challenge,
                "comparison_status": irow.get("comparison_status"),
                "comparable": bool(irow.get("comparable")),
                "control_failures": list(irow.get("control_failures") or []),
                "adversarial_result_identity": arow.get("adversarial_result_identity"),
                "adversarial_qualified": bool(arow.get("qualified")),
                "paraphrase_consistency_percent": arow.get("paraphrase_consistency_percent"),
                "pressure_resistance_percent": arow.get("pressure_resistance_percent"),
                "false_premise_rejection_percent": arow.get("false_premise_rejection_percent"),
                "canary_consistency_percent": arow.get("canary_consistency_percent"),
                "fresh_shadow_consistency_percent": arow.get("fresh_shadow_consistency_percent"),
                "critical_adversarial_failures": list(
                    arow.get("critical_adversarial_failures") or []
                ),
            }
        )
    return rows


def game_tape(
    result_paths: Iterable[Path],
    *,
    integrity_paths: Iterable[Path],
    adversarial_paths: Iterable[Path],
    capability_path: Path,
    recertification_path: Path | None = None,
) -> dict[str, Any]:
    """Build one fully traceable, read-only certificate investigation projection."""

    result_paths = [Path(path) for path in result_paths]
    integrity_paths = [Path(path) for path in integrity_paths]
    adversarial_paths = [Path(path) for path in adversarial_paths]
    stability = stability_report(result_paths)
    run_ids = list(stability["candidate_run_identities"])
    results = _result_by_run(result_paths, stability)

    certificate = validate_capability_certificate(
        _load_json(capability_path, label="capability certificate")
    )
    recomputed = capability_certificate(
        result_paths,
        integrity_paths=integrity_paths,
        adversarial_paths=adversarial_paths,
        issued_at_utc=str(certificate["issued_at_utc"]),
    )
    if (
        recomputed["capability_certificate_identity"]
        != certificate["capability_certificate_identity"]
    ):
        raise ValueError(
            "Game Tape capability certificate cannot be reproduced from supplied evidence"
        )
    if recomputed != certificate:
        raise ValueError(
            "Game Tape capability certificate bytes drift from authoritative recomputation"
        )

    recertification: dict[str, Any] | None = None
    if recertification_path is not None:
        recertification = validate_recertification_plan(
            _load_json(recertification_path, label="differential recertification plan")
        )
        if recertification.get("previous_capability_certificate_identity") != certificate.get(
            "capability_certificate_identity"
        ):
            raise ValueError(
                "Game Tape recertification plan is not bound to the capability certificate"
            )
        if recertification.get("candidate_configuration_identity") != certificate.get(
            "candidate_configuration_identity"
        ):
            raise ValueError(
                "Game Tape recertification candidate identity does not match certificate"
            )

    concepts = _concept_projection(run_ids, results, stability)
    questions = _question_projection(run_ids, results)
    evidence = _evidence_projection(run_ids, results)
    consistency = _consistency_projection(run_ids, results)
    benchmark = _benchmark_projection(run_ids, results, integrity_paths, adversarial_paths)

    recertify_concepts = list((recertification or {}).get("recertification_concepts") or [])
    recertify_questions = list((recertification or {}).get("recertification_question_ids") or [])
    failed_run_questions = sorted(
        {
            question["id"]
            for result in results.values()
            for question in result.get("questions", [])
            if isinstance(question, dict) and question.get("critical_failed")
        },
        key=lambda qid: int(str(qid)[1:]),
    )
    tabs = {
        "overview": {
            "source_version": certificate.get("source_version"),
            "repository_identity": certificate.get("repository_identity"),
            "candidate_configuration_identity": certificate.get("candidate_configuration_identity"),
            "qualification_profile": certificate.get("qualification_profile"),
            "authority_class": certificate.get("authority_class"),
            "certificate_status": certificate.get("certificate_status"),
            "granted_tier": certificate.get("granted_tier"),
            "authorization": certificate.get("authorization"),
            "runs": {
                "qualified": certificate.get("passes"),
                "required": certificate.get("runs_required"),
            },
            "median_composite_understanding": certificate.get("median_composite_understanding"),
            "concepts": {
                "certified": len(certificate.get("certified_concepts") or []),
                "critical": len(certificate.get("critical_concepts") or []),
                "recertification_required": len(recertify_concepts),
            },
            "what_remains": {
                "critical_failed_questions": failed_run_questions,
                "capability_gate_failures": list(certificate.get("gate_failures") or []),
                "recertification_concepts": recertify_concepts,
                "recertification_question_ids": recertify_questions,
            },
        },
        "concepts": concepts,
        "questions": questions,
        "evidence": evidence,
        "consistency_epistemics": consistency,
        "stability": stability,
        "benchmark_integrity": benchmark,
        "qualification_capability": {
            "capability_certificate_identity": certificate.get("capability_certificate_identity"),
            "certificate_status": certificate.get("certificate_status"),
            "target_tier": certificate.get("target_tier"),
            "granted_tier": certificate.get("granted_tier"),
            "authorization": certificate.get("authorization"),
            "gate_failures": list(certificate.get("gate_failures") or []),
            "freshness": certificate.get("freshness"),
            "enforcement_status": certificate.get("enforcement_status"),
            "enforcement_note": certificate.get("enforcement_note"),
        },
        "recertification": recertification,
    }
    input_identities = {
        "run_certificate_identities": [results[run].get("certificate_identity") for run in run_ids],
        "stability_certificate_identity": stability.get("stability_certificate_identity"),
        "benchmark_integrity_result_identities": [
            row.get("integrity_result_identity") for row in benchmark
        ],
        "adversarial_result_identities": [
            row.get("adversarial_result_identity") for row in benchmark
        ],
        "capability_certificate_identity": certificate.get("capability_certificate_identity"),
        "recertification_plan_identity": (
            None
            if recertification is None
            else recertification.get("recertification_plan_identity")
        ),
    }
    base = {
        "artifact_type": "repository-understanding-game-tape",
        "schema_version": GAME_TAPE_SCHEMA_VERSION,
        "game_tape_policy_version": GAME_TAPE_POLICY_VERSION,
        "source_version": certificate.get("source_version"),
        "repository_identity": certificate.get("repository_identity"),
        "candidate_configuration_identity": certificate.get("candidate_configuration_identity"),
        "tab_order": list(GAME_TAPE_TABS),
        "authority_contract": dict(_AUTHORITY_CONTRACT),
        "input_identities": input_identities,
        "tabs": tabs,
    }
    return {**base, "game_tape_identity": "sha256:" + _canonical_sha256(base)}


def validate_game_tape(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("artifact_type") != "repository-understanding-game-tape":
        raise ValueError("Game Tape artifact_type mismatch")
    if payload.get("schema_version") != GAME_TAPE_SCHEMA_VERSION:
        raise ValueError("Game Tape schema mismatch")
    if payload.get("game_tape_policy_version") != GAME_TAPE_POLICY_VERSION:
        raise ValueError("Game Tape policy mismatch")
    if payload.get("tab_order") != list(GAME_TAPE_TABS):
        raise ValueError("Game Tape tab order is not canonical")
    if payload.get("authority_contract") != _AUTHORITY_CONTRACT:
        raise ValueError("Game Tape authority contract drifted")
    identity = payload.get("game_tape_identity")
    if not isinstance(identity, str) or not identity.startswith("sha256:"):
        raise ValueError("Game Tape identity is invalid")
    base = {key: value for key, value in payload.items() if key != "game_tape_identity"}
    if identity != "sha256:" + _canonical_sha256(base):
        raise ValueError("Game Tape identity does not match artifact bytes")
    tabs = payload.get("tabs")
    if not isinstance(tabs, dict) or list(tabs) != list(GAME_TAPE_TABS):
        raise ValueError("Game Tape tabs are incomplete or reordered")
    return payload


def game_tape_policy_manifest() -> dict[str, Any]:
    return {
        "policy_version": GAME_TAPE_POLICY_VERSION,
        "schema_version": GAME_TAPE_SCHEMA_VERSION,
        "tabs": list(GAME_TAPE_TABS),
        "authority_contract": dict(_AUTHORITY_CONTRACT),
        "lineage_rule": (
            "recompute stability/capability from supplied evidence and require exact capability identity before projection"
        ),
        "secret_truth_projection": "forbidden; project result/evidence outcomes and identities, not SECRET expected answer text",
    }


def human_game_tape_report(payload: dict[str, Any]) -> str:
    tape = validate_game_tape(payload)
    overview = tape["tabs"]["overview"]
    remains = overview["what_remains"]
    lines = [
        "OH-GOON QA GAME TAPE",
        "",
        f"Repository             {overview['repository_identity']}",
        f"Source version         {overview['source_version']}",
        f"Qualification profile  {overview['qualification_profile']}",
        f"Certificate            {overview['certificate_status']}",
        f"Capability tier        {overview['granted_tier'] or 'NONE'}",
        f"Stable runs            {overview['runs']['qualified']}/{overview['runs']['required']}",
        f"Median understanding   {overview['median_composite_understanding']}",
        "",
        "What remains",
        f"  capability gates     {len(remains['capability_gate_failures'])}",
        f"  critical questions   {len(remains['critical_failed_questions'])}",
        f"  recert concepts      {len(remains['recertification_concepts'])}",
        f"  recert questions     {len(remains['recertification_question_ids'])}",
        "",
        "Views",
    ]
    for tab in GAME_TAPE_TABS:
        lines.append(f"  {tab}")
    lines.extend(
        [
            "",
            "Authority",
            "  Projection only. Existing grading/certificate/runtime owners remain authoritative.",
            f"Game Tape identity     {tape['game_tape_identity']}",
        ]
    )
    return "\n".join(lines) + "\n"
