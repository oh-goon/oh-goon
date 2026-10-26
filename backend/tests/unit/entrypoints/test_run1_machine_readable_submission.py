"""RUN-1: one Fleet owner, machine-readable Run submission and terminal projection."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import _run
from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus
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
