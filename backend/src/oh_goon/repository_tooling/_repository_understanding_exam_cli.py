"""CLI parsing and mechanical dispatch for repository-understanding exam commands.

The public exam module retains authority by passing its live globals mapping into
``dispatch``. This keeps monkeypatch/authority seams stable while moving parser and
serialization mechanics out of the public owner.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def build_parser(*, description: str | None = None) -> argparse.ArgumentParser:
    from oh_goon.repository_tooling.repository_understanding_capability import CAPABILITY_ACTIONS
    from oh_goon.repository_tooling.repository_understanding_policy import (
        DEFAULT_QUALIFICATION_PROFILE,
        QUALIFICATION_PROFILES,
    )

    parser = argparse.ArgumentParser(description=description)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="create an answer-free exam checkout")
    prepare_parser.add_argument("destination", type=Path); prepare_parser.add_argument("--force", action="store_true")
    verify_parser = subparsers.add_parser("verify", help="validate PUBLIC/EVIDENCE/SECRET exam material"); verify_parser.add_argument("--manifest", type=Path); verify_parser.add_argument("--quiet", action="store_true", help="emit only a concise success summary")
    template_parser = subparsers.add_parser("grade-template", help="write semantic grader observations after candidate answers are frozen")
    template_parser.add_argument("--submission", type=Path, required=True); template_parser.add_argument("--candidate"); template_parser.add_argument("--mode")
    template_parser.add_argument("--candidate-configuration", type=Path, help="frozen candidate/model/harness configuration JSON; required for repeated-run stability")
    template_parser.add_argument("--candidate-run-identity", help="opaque harness run identity; required and unique across repeated-run stability inputs")
    template_parser.add_argument("--profile", choices=tuple(sorted(QUALIFICATION_PROFILES)), default=DEFAULT_QUALIFICATION_PROFILE, help="authority/risk qualification profile; never selected from candidate content")
    template_parser.add_argument("--output", type=Path, required=True)
    calibrate_parser = subparsers.add_parser("calibrate", help="compute empirical question telemetry without changing grading policy"); calibrate_parser.add_argument("observations", type=Path); calibrate_parser.add_argument("--output", type=Path)
    human_baseline_parser = subparsers.add_parser("human-baseline", help="compute expert timing/disagreement evidence without changing bank policy"); human_baseline_parser.add_argument("observations", type=Path); human_baseline_parser.add_argument("--output", type=Path)
    lifecycle_parser = subparsers.add_parser("lifecycle-transition", help="authorize one explicit SECRET question lifecycle transition without mutating the bank")
    lifecycle_parser.add_argument("request", type=Path); lifecycle_parser.add_argument("--calibration-report", type=Path); lifecycle_parser.add_argument("--human-baseline-report", type=Path); lifecycle_parser.add_argument("--output", type=Path)
    stability_parser = subparsers.add_parser("stability", help="combine exactly three identity-bound scored runs into stability qualification evidence"); stability_parser.add_argument("results", type=Path, nargs="+"); stability_parser.add_argument("--output", type=Path)
    capability_parser = subparsers.add_parser("capability-certificate", help="derive a capability-scoped certificate from three stable, comparable, adversarial-clean runs")
    capability_parser.add_argument("results", type=Path, nargs=3); capability_parser.add_argument("--integrity", type=Path, nargs=3, required=True); capability_parser.add_argument("--adversarial", type=Path, nargs=3, required=True); capability_parser.add_argument("--issued-at"); capability_parser.add_argument("--output", type=Path)
    capability_check_parser = subparsers.add_parser("capability-check", help="evaluate one certificate against exact repository/candidate identity and requested capability")
    capability_check_parser.add_argument("certificate", type=Path); capability_check_parser.add_argument("--repository-identity", required=True); capability_check_parser.add_argument("--candidate-configuration-identity", required=True); capability_check_parser.add_argument("--requested-capability", choices=CAPABILITY_ACTIONS, required=True); capability_check_parser.add_argument("--output", type=Path)
    recertification_parser = subparsers.add_parser("recertification-plan", help="compare one prior capability/result snapshot with current source and emit concept-bound retest scope")
    recertification_parser.add_argument("certificate", type=Path); recertification_parser.add_argument("prior_result", type=Path); recertification_parser.add_argument("--output", type=Path)
    challenge_parser = subparsers.add_parser("challenge-set", help="assemble one identity-bound SECRET adversarial/canary challenge set"); challenge_parser.add_argument("--seed", required=True); challenge_parser.add_argument("--output", type=Path)
    adversarial_parser = subparsers.add_parser("adversarial", help="score structured adversarial/paraphrase observations without changing semantic truth"); adversarial_parser.add_argument("challenge_set", type=Path); adversarial_parser.add_argument("observations", type=Path); adversarial_parser.add_argument("--output", type=Path)
    integrity_parser = subparsers.add_parser("integrity", help="validate harness/control-plane evidence required for benchmark comparability"); integrity_parser.add_argument("execution_evidence", type=Path); integrity_parser.add_argument("--challenge-set", type=Path); integrity_parser.add_argument("--output", type=Path)
    score_parser = subparsers.add_parser("score", help="apply deterministic scoring/qualification policy to grader observations"); score_parser.add_argument("grades", type=Path); score_parser.add_argument("--submission", type=Path); score_parser.add_argument("--output", type=Path)
    regrade_parser = subparsers.add_parser("regrade", help="recompute a persisted result from its offline regrade bundle"); regrade_parser.add_argument("result", type=Path); regrade_parser.add_argument("--output", type=Path)
    game_tape_parser = subparsers.add_parser("game-tape", help="project stable/capability evidence into one read-only QA investigation artifact")
    game_tape_parser.add_argument("results", type=Path, nargs=3); game_tape_parser.add_argument("--integrity", type=Path, nargs=3, required=True); game_tape_parser.add_argument("--adversarial", type=Path, nargs=3, required=True); game_tape_parser.add_argument("--certificate", type=Path, required=True); game_tape_parser.add_argument("--recertification", type=Path); game_tape_parser.add_argument("--output", type=Path)
    game_tape_report_parser = subparsers.add_parser("game-tape-report", help="render a concise human summary from a QA Game Tape artifact"); game_tape_report_parser.add_argument("game_tape", type=Path)
    report_parser = subparsers.add_parser("report", help="render a concise human report from a scored result artifact"); report_parser.add_argument("result", type=Path)
    return parser


def _emit(payload: object, output: Path | None) -> None:
    body = json.dumps(payload, indent=2) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(body, encoding="utf-8")
    print(body, end="")


def _prepare(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    print(api["prepare"](args.destination, force=args.force))
    return 0


def _verify(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = api["verify"](manifest_path=args.manifest)
    if args.quiet:
        print(
            "REPOSITORY UNDERSTANDING EXAM: PASS "
            f"(source={payload['source_version']}, questions={payload['question_count']}, "
            f"qualification={payload['qualification_question_count']}, critical={payload['critical_question_count']})"
        )
    else:
        print(json.dumps(payload, indent=2))
    return 0


def _grade_template(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = api["grade_template"](
        submission=args.submission,
        candidate=args.candidate,
        mode=args.mode,
        qualification_profile=args.profile,
        candidate_configuration=args.candidate_configuration,
        candidate_run_identity=args.candidate_run_identity,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


def _calibrate(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    snapshot = api["exam_snapshot"]()
    questions = snapshot["questions"]
    payload = api["calibration_report"](
        args.observations,
        question_ids=tuple(q["id"] for q in questions),
        authored_difficulty={q["id"]: q["public"]["difficulty"] for q in questions},
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
    )
    _emit(payload, args.output)
    return 0


def _human_baseline(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    snapshot = api["exam_snapshot"]()
    payload = api["human_baseline_report"](
        args.observations,
        question_ids=tuple(q["id"] for q in snapshot["questions"]),
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
    )
    _emit(payload, args.output)
    return 0


def _lifecycle_transition(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    calibration = (
        json.loads(args.calibration_report.read_text(encoding="utf-8"))
        if args.calibration_report is not None
        else None
    )
    human = (
        json.loads(args.human_baseline_report.read_text(encoding="utf-8"))
        if args.human_baseline_report is not None
        else None
    )
    payload = api["authorize_question_transition"](
        args.request,
        lifecycle=api["question_lifecycle"](),
        question_audit=api["question_audit"](),
        calibration_report=calibration,
        human_baseline=human,
    )
    _emit(payload, args.output)
    return 0


def _stability(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    _emit(api["stability_report"](args.results), args.output)
    return 0


def _capability_certificate(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = api["capability_certificate"](
        args.results,
        integrity_paths=args.integrity,
        adversarial_paths=args.adversarial,
        issued_at_utc=args.issued_at,
    )
    _emit(payload, args.output)
    return 0


def _capability_check(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = api["capability_check"](
        args.certificate,
        repository_identity=args.repository_identity,
        candidate_configuration_identity=args.candidate_configuration_identity,
        requested_capability=args.requested_capability,
    )
    _emit(payload, args.output)
    return 0


def _recertification_plan(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = api["differential_recertification_plan"](
        args.certificate, args.prior_result, current_snapshot=api["exam_snapshot"]()
    )
    _emit(payload, args.output)
    return 0


def _challenge_set(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    snapshot = api["exam_snapshot"]()
    payload = api["build_challenge_set"](
        api["benchmark_registry"](),
        questions=api["load_exam"](),
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
        seed=args.seed,
    )
    _emit(payload, args.output)
    return 0


def _adversarial(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = api["adversarial_report"](
        args.challenge_set, args.observations, registry=api["benchmark_registry"]()
    )
    _emit(payload, args.output)
    return 0


def _integrity(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    snapshot = api["exam_snapshot"]()
    payload = api["benchmark_integrity_report"](
        args.execution_evidence,
        registry=api["benchmark_registry"](),
        source_version=snapshot["source_version"],
        repository_identity=snapshot["repository_identity"],
        exam_snapshot_sha256=snapshot["snapshot_sha256"],
        challenge_instance_path=args.challenge_set,
    )
    _emit(payload, args.output)
    return 0


def _score(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    _emit(api["score"](args.grades, submission=args.submission), args.output)
    return 0


def _regrade(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    _emit(api["score"](args.result, submission=None), args.output)
    return 0


def _game_tape(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = api["game_tape"](
        args.results,
        integrity_paths=args.integrity,
        adversarial_paths=args.adversarial,
        capability_path=args.certificate,
        recertification_path=args.recertification,
    )
    _emit(payload, args.output)
    return 0


def _game_tape_report(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = json.loads(args.game_tape.read_text(encoding="utf-8"))
    print(api["human_game_tape_report"](payload), end="")
    return 0


def _report(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("result input must be a JSON object")
    print(api["human_report"](payload), end="")
    return 0


_HANDLERS = {
    "prepare": _prepare,
    "verify": _verify,
    "grade-template": _grade_template,
    "calibrate": _calibrate,
    "human-baseline": _human_baseline,
    "lifecycle-transition": _lifecycle_transition,
    "stability": _stability,
    "capability-certificate": _capability_certificate,
    "capability-check": _capability_check,
    "recertification-plan": _recertification_plan,
    "challenge-set": _challenge_set,
    "adversarial": _adversarial,
    "integrity": _integrity,
    "score": _score,
    "regrade": _regrade,
    "game-tape": _game_tape,
    "game-tape-report": _game_tape_report,
    "report": _report,
}


def dispatch(args: argparse.Namespace, api: Mapping[str, Any]) -> int:
    try:
        handler = _HANDLERS[args.command]
    except KeyError as exc:
        raise AssertionError(args.command) from exc
    return handler(args, api)
