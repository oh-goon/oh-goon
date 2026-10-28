"""RUN-1: one Fleet owner, machine-readable Run submission and terminal projection."""

from __future__ import annotations

import json
import shlex
from types import SimpleNamespace
from typing import cast

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_plan_run_readback import print_run_completion
from oh_goon.cli_runtime_plans import _run
from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    MissingPlanArtifact,
    PlanRetainedContentEvidence,
    PresentPlanArtifact,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.game_tape import GameTapeWhyNotDiagnostic
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus, GameTapeStatus
from tests.factories.fleet_plan_targets import structural_fleet_plan_target

_PLAN = ResolvedFleetPlan.create(
    name="submitted",
    description="",
    execution=VerificationExecutionPolicy(),
    steps=(ResolvedCommandStep(step_id="verify", argv=("true",), timeout_seconds=30),),
)


def _args(*flags: str):
    # Direct handler proof uses the owner-only resolved invocation transport.
    args = build_parser().parse_args(
        [
            "dev",
            "_run-plan",
            "some-plan.yml",
            "--repository-id",
            "repo-1",
            "--lock-file",
            "goon.lock",
            *flags,
        ]
    )
    args.command, args.plan_command = "plan", "run"
    return args


class _Plans:
    def __init__(self, *, reject: bool = False):
        self.calls: list[object] = []
        self.reject = reject

    def require_cli_plan_preparation(self, fleet_run_id):
        self.calls.append(("preflight", fleet_run_id))
        if self.reject:
            raise InvalidRequest("Run was not enrolled")

    def run(self, path, **kwargs):
        self.calls.append(("run", path, kwargs))
        return SimpleNamespace(
            fleet_run_id="fleet-1",
            status=FleetRunStatus.QUEUED,
            target=structural_fleet_plan_target(_PLAN),
        )


def _runtime(*, reject: bool = False):
    return SimpleNamespace(plans=_Plans(reject=reject), runs=SimpleNamespace())


def test_async_run_json_is_one_admitted_owner_result(capsys):
    runtime = _runtime()
    assert _run(runtime, _args("--json", "--strict", "--no-wait", "--fleet-run-id", "fleet-1")) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == {
        "schema": "oh-goon.plan-run-result/v1",
        "fleet_run_id": "fleet-1",
        "plan_id": _PLAN.plan_id,
        "status": "QUEUED",
        "outcome": None,
    }
    assert [row[0] for row in runtime.plans.calls] == ["preflight", "run"]
    assert runtime.plans.calls[1][2]["repository_ids"] == {"repo-1"}


@pytest.mark.parametrize(
    "outcome,status,code",
    [
        (FleetRunOutcome.SUCCESS, FleetRunStatus.COMPLETED, 0),
        (FleetRunOutcome.FAILED, FleetRunStatus.FAILED, 1),
        (FleetRunOutcome.CANCELLED, FleetRunStatus.CANCELLED, 1),
    ],
)
def test_waited_run_json_reuses_original_terminal_classification_without_stdout_progress(
    monkeypatch,
    capsys,
    outcome,
    status,
    code,
):
    from oh_goon import cli_runtime_plans as command

    def fake_wait(runtime, run_id, *, admitted_run):
        assert admitted_run.fleet_run_id == run_id == "fleet-1"
        command._progress_print("Observed activity: real owner")
        return SimpleNamespace(run=SimpleNamespace(status=status), outcome=outcome)

    monkeypatch.setattr(command, "_wait_for_terminal_with_progress", fake_wait)
    runtime = _runtime()
    assert _run(runtime, _args("--json")) == code
    streams = capsys.readouterr()
    assert streams.err == "Observed activity: real owner\n"
    value = json.loads(streams.out)
    assert value["status"] == status.value
    assert value["outcome"] == outcome.value
    assert value["fleet_run_id"] == "fleet-1"
    assert len(streams.out.strip().splitlines()) == 1


def test_unenrolled_run_is_rejected_before_resolution_without_false_no_effect_claim(capsys):
    runtime = _runtime(reject=True)
    assert _run(runtime, _args("--json")) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "error"
    assert value["fleet_run_id"] is None
    assert "execution" not in value
    assert runtime.plans.calls == [("preflight", None)]


def test_json_repair_pair_rejects_before_reading_plan_or_fleet(capsys):
    runtime = _runtime()
    assert _run(runtime, _args("--json", "--wait", "--repair-handoff")) == 2
    assert not runtime.plans.calls
    assert "cannot be combined" in json.loads(capsys.readouterr().out)["message"]


def test_async_human_run_reports_the_admitted_run(capsys):
    runtime = _runtime()
    assert _run(runtime, _args("--strict", "--no-wait")) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Fleet Run ID: fleet-1",
        f"Plan ID: {_PLAN.plan_id}",
        "Status: QUEUED",
    ]


@pytest.mark.parametrize(
    "outcome,status,code",
    [
        (FleetRunOutcome.SUCCESS, FleetRunStatus.COMPLETED, 0),
        (FleetRunOutcome.ATTENTION_REQUIRED, FleetRunStatus.COMPLETED, 1),
    ],
)
def test_waited_human_completion_shows_recorded_outputs_without_changing_outcome(
    monkeypatch, capsys, outcome, status, code
):
    from oh_goon import cli_runtime_plans as command

    step = SimpleNamespace(
        step_id="train",
        status=GameTapeStatus.PASSED,
        outputs=(PublishedPlanOutputEvidence(name="accuracy", sha256="a" * 64, byte_count=4),),
        artifacts=(PresentPlanArtifact(path="model.pkl", sha256="b" * 64, byte_count=104),),
    )
    detail = SimpleNamespace(
        run=SimpleNamespace(status=status, fleet_run_id="fleet-1"),
        outcome=outcome,
        game_tape=SimpleNamespace(
            why_not=None,
            repositories=(SimpleNamespace(repository=SimpleNamespace(name="iris"), steps=(step,)),),
        ),
    )
    monkeypatch.setattr(command, "_wait_for_terminal_with_progress", lambda *args, **kwargs: detail)
    runtime = _runtime()
    assert _run(runtime, _args()) == code
    output = capsys.readouterr().out
    assert f"Execution status: {status.value}" in output
    assert f"Outcome: {outcome.value}" in output
    assert "Output evidence: iris/train/accuracy (4 bytes; sha256:" + "a" * 64 in output
    assert "Artifact: iris/train: model.pkl [PRESENT]" in output
    assert "Evidence: 104 bytes; sha256:" + "b" * 64 in output
    assert "Retained reference" not in output
    assert "Failed Step" not in output
    assert "Inspect Run: oh-goon plan handoff fleet-1" in output
    assert "Inspect outputs: oh-goon plan outputs fleet-1" in output
    assert "Read logs (while available): oh-goon plan logs fleet-1" in output
    assert [row[0] for row in runtime.plans.calls] == ["preflight", "run"]


def test_waited_human_failure_shows_original_guidance_and_failed_step(monkeypatch, capsys):
    from oh_goon import cli_runtime_plans as command

    failure = GameTapeWhyNotDiagnostic(
        code="NATIVE_FAILURE",
        stage="step",
        retryable=False,
        authority_owner="fleet_run",
        message="The original native training command failed.",
        safe_next_action="Correct the recorded training input before starting another Run.",
    )
    step = SimpleNamespace(
        step_id="train",
        status=GameTapeStatus.FAILED,
        exit_code=7,
        reason="Native command exited with status 7",
        outputs=(),
        artifacts=(MissingPlanArtifact(path="model.pkl"),),
    )
    not_run = SimpleNamespace(
        step_id="publish", status=GameTapeStatus.NOT_RUN, outputs=(), artifacts=()
    )
    detail = SimpleNamespace(
        run=SimpleNamespace(status=FleetRunStatus.FAILED, fleet_run_id="fleet-1"),
        outcome=FleetRunOutcome.FAILED,
        game_tape=SimpleNamespace(
            why_not=failure,
            repositories=(
                SimpleNamespace(repository=SimpleNamespace(name="iris"), steps=(step, not_run)),
            ),
        ),
    )
    monkeypatch.setattr(command, "_wait_for_terminal_with_progress", lambda *args, **kwargs: detail)
    assert _run(_runtime(), _args()) == 1
    output = capsys.readouterr().out
    assert f"Failure: {failure.code}: {failure.message}" in output
    assert f"Next action: {failure.safe_next_action}" in output
    assert "Failed Step: iris/train (exit 7)" in output
    assert "Recorded reason: Native command exited with status 7" in output
    assert "Artifact: iris/train: model.pkl [MISSING]" in output
    assert "Failed Step: iris/publish" not in output


def test_completion_preview_reports_omissions_retention_and_shell_safe_readback(capsys):
    run_id = "fleet-1; echo '$literal'"
    retained = PlanRetainedContentEvidence(
        kind="file",
        sha256="a" * 64,
        byte_count=12,
        entry_count=1,
        reference_id="ref-" + "a" * 32,
        owner_repository_id="repo-" + "b" * 20,
    )
    steps = tuple(
        SimpleNamespace(
            step_id=f"check-{index}",
            status=GameTapeStatus.FAILED,
            exit_code=1,
            reason=None,
            outputs=(
                PublishedPlanOutputEvidence(name=f"result_{index}", sha256="a" * 64, byte_count=12),
            ),
            artifacts=(
                PresentPlanArtifact(
                    path=f"report-{index}.json",
                    sha256="a" * 64,
                    byte_count=12,
                    retained_object=retained,
                ),
            ),
        )
        for index in range(9)
    )
    detail = SimpleNamespace(
        run=SimpleNamespace(fleet_run_id=run_id),
        game_tape=SimpleNamespace(
            why_not=None,
            repositories=(
                SimpleNamespace(
                    repository=SimpleNamespace(name="project", repository_id="repo-1"),
                    steps=steps,
                ),
            ),
        ),
    )
    print_run_completion(cast(FleetRunDetail, detail))
    output = capsys.readouterr().out
    assert output.count("Failed Step:") == 8
    assert output.count("Output evidence:") == 8
    assert output.count("Artifact:") == 8
    assert "More failed Steps are recorded" in output
    assert "More output evidence is recorded" in output
    assert "More artifacts are recorded" in output
    assert "Retained reference: " + retained.reference_id in output
    readback = next(
        line.removeprefix("Inspect Run: ")
        for line in output.splitlines()
        if line.startswith("Inspect Run: ")
    )
    assert shlex.split(readback) == ["oh-goon", "plan", "handoff", run_id]
    export = next(
        line.removeprefix("  Export artifact: ")
        for line in output.splitlines()
        if line.startswith("  Export artifact: ")
    )
    assert shlex.split(export) == [
        "oh-goon",
        "plan",
        "export-artifact",
        run_id,
        "--repository-id",
        "repo-1",
        "--step",
        "check-0",
        "--path",
        "report-0.json",
        "--output",
        "./report-0.json",
    ]


@pytest.mark.parametrize("status", [FleetRunStatus.RUNNING, FleetRunStatus.CANCELLED])
def test_wait_interruption_requests_original_durable_cancellation(monkeypatch, capsys, status):
    from unittest.mock import Mock

    from oh_goon import cli_runtime_plans as command

    runtime = _runtime()
    runtime.runs.cancel = Mock(return_value=SimpleNamespace(status=status))
    monkeypatch.setattr(
        command, "_wait_for_terminal_with_progress", Mock(side_effect=KeyboardInterrupt)
    )
    assert _run(runtime, _args("--json")) == 130
    runtime.runs.cancel.assert_called_once_with("fleet-1")
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "interrupted"
    assert receipt["cancellation_requested"] is True
    assert receipt["execution_status"] == status.value
    assert receipt["execution_terminal"] is status.terminal
    assert receipt["cleanup"] == "not-established-by-interruption"
    assert receipt["readback_argv"] == ["oh-goon", "plan", "handoff", "fleet-1", "--json"]


def test_interrupted_cancel_refusal_retains_durable_readback_and_uncertainty(monkeypatch, capsys):
    from unittest.mock import Mock

    from oh_goon import cli_runtime_plans as command

    runtime = _runtime()
    runtime.runs.cancel = Mock(side_effect=InvalidRequest("Unavailable cancellation"))
    monkeypatch.setattr(
        command, "_wait_for_terminal_with_progress", Mock(side_effect=KeyboardInterrupt)
    )
    assert _run(runtime, _args("--json")) == 130
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["cancellation_requested"] is False
    assert receipt["execution_status"] is receipt["execution_terminal"] is None
    assert receipt["readback_argv"][3] == "fleet-1"
