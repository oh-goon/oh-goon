"""RUN-1: one Fleet owner, machine-readable Run submission and terminal projection."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import _run
from oh_goon.core.errors import InvalidRequest
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


def _args(*flags: str):
    return build_parser().parse_args(
        [
            "plan",
            "run",
            "some-plan.yml",
            "--repository-id",
            "repo-1",
            *flags,
        ]
    )


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
            target=SimpleNamespace(plan=SimpleNamespace(plan_id="plan-sha")),
        )


def _runtime(*, reject: bool = False):
    return SimpleNamespace(plans=_Plans(reject=reject), runs=SimpleNamespace())


def test_async_run_json_is_one_admitted_owner_result(capsys):
    runtime = _runtime()
    assert _run(runtime, _args("--json", "--fleet-run-id", "fleet-1")) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == {
        "schema": "oh-goon.plan-run-result/v1",
        "fleet_run_id": "fleet-1",
        "plan_id": "plan-sha",
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
    assert _run(runtime, _args("--json", "--wait")) == code
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


def test_default_human_run_output_is_unchanged(capsys):
    runtime = _runtime()
    assert _run(runtime, _args()) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Fleet Run ID: fleet-1",
        "Plan ID: plan-sha",
        "Status: QUEUED",
    ]
