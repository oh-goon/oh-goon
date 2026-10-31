"""Runs navigation transports the original scoped history and transient cursors."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_run_readback import run_plan_readback
from oh_goon.cli_result import CliTermination
from oh_goon.core.errors import ErrorKind, InvalidRequest
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.game_tape_live_output import (
    GameTapeLiveOutputBatch,
    GameTapeLiveOutputChunk,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    FailedFleetRun,
    FleetRunDetail,
    FleetRunFailure,
    FleetRunHistoryItem,
    FleetRunHistoryPage,
    FleetRunProgress,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunStatus,
    GameTapeFailureNavigationState,
    OperationAction,
)
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.run_admission import repository_admissions_for


@pytest.fixture
def detail(tmp_path: Path) -> FleetRunDetail:
    repository = RepositoryIdentity.model_validate(
        {"path": tmp_path / "repository", "name": "selected"}
    )
    plan = ResolvedFleetPlan.create(
        name="quality",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="check", argv=("echo", "PRIVATE_ARGV_CANARY")),),
    )
    target = structural_fleet_plan_target(plan)
    now = utc_now()
    return FleetRunDetail(
        journal_revision=12,
        run=FailedFleetRun(
            fleet_run_id="selected-run",
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            repository_scope=fleet_repository_scope({repository.repository_id}),
            targets=(repository,),
            accepted_at=now,
            finished_at=now,
            progress=FleetRunProgress(total=1, succeeded=0, failed=0),
            status=FleetRunStatus.FAILED,
            failure=FleetRunFailure(
                code="ORIGINAL_REFUSAL",
                kind=ErrorKind.CONFLICT,
                stage=FleetRunFailureStage.SUBMISSION,
                message="Original owner refused this Run",
            ),
        ),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, [repository]),
    )


class _Owner:
    def __init__(self, detail: FleetRunDetail) -> None:
        self.detail = detail
        self.page = FleetRunHistoryPage(
            journal_revision=12,
            items=(
                FleetRunHistoryItem(
                    admitted_revision=7,
                    run=detail.run,
                    execution=detail.execution,
                ),
            ),
            next_before_revision=7,
        )
        self.history_calls: list[dict[str, object]] = []
        self.reads: list[str] = []
        self.cursors: list[int] = []
        self.batches: list[GameTapeLiveOutputBatch] = []

    def history(self, **options: object) -> FleetRunHistoryPage:
        self.history_calls.append(options)
        return self.page

    def get(self, run_id: str) -> FleetRunDetail:
        self.reads.append(run_id)
        assert run_id == self.detail.run.fleet_run_id
        return self.detail

    def game_tape_live_output(self, run_id: str, *, after: int) -> GameTapeLiveOutputBatch:
        assert run_id == self.detail.run.fleet_run_id
        self.cursors.append(after)
        return self.batches.pop(0)


def _invoke(owner: _Owner, command: str, **options: object) -> int:
    values: dict[str, object] = {
        "command": "runs",
        "runs_command": command,
        "fleet_run_id": owner.detail.run.fleet_run_id,
        "as_json": True,
        "limit": 20,
        "before_revision": None,
        "after": 0,
        "last": False,
        "follow": False,
        "repository_id": None,
        "plan_id": None,
    }
    values.update(options)
    return run_plan_readback(
        cast(ApplicationRuntime, SimpleNamespace(runs=owner)), argparse.Namespace(**values)
    )


def _batch(state: str, sequence: int, *, dropped: int = 0) -> GameTapeLiveOutputBatch:
    return GameTapeLiveOutputBatch.model_validate(
        {
            "fleet_run_id": "selected-run",
            "state": state,
            "available": state != "UNAVAILABLE",
            "expires_at": utc_now() + timedelta(seconds=30) if state == "SEALED" else None,
            "chunks": ()
            if state == "UNAVAILABLE"
            else (
                GameTapeLiveOutputChunk(
                    sequence=sequence,
                    repository_id="recorded-repository",
                    step_id="check",
                    attempt=1,
                    stream="stdout",
                    line_start=sequence,
                    line_end=sequence,
                    text=f"recorded chunk {sequence}",
                    emitted_at=utc_now(),
                ),
            ),
            "next_sequence": sequence,
            "retained_from_sequence": sequence,
            "dropped_chunk_count": dropped,
            "failure_navigation_state": GameTapeFailureNavigationState.UNAVAILABLE
            if state == "UNAVAILABLE"
            else GameTapeFailureNavigationState.AVAILABLE,
        }
    )


def test_list_preserves_original_cursor_and_compact_facts(
    detail: FleetRunDetail, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = _Owner(detail)
    assert _invoke(owner, "list", limit=1, before_revision=10) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["next_before_revision"] == owner.page.next_before_revision
    assert result["journal_revision"] == owner.page.journal_revision
    assert result["items"][0]["fleet_run_id"] == detail.run.fleet_run_id
    assert result["items"][0]["outcome"] == detail.outcome.value
    assert result["items"][0]["targets"] == [
        repository.model_dump(mode="json") for repository in detail.run.targets
    ]
    assert "PRIVATE_ARGV_CANARY" not in output
    assert owner.history_calls == [
        {"limit": 1, "before_revision": 10, "repository_id": None, "plan_id": None}
    ]
    assert owner.reads == []


def test_last_selects_exact_plan_repository_scope_before_one_original_read(
    detail: FleetRunDetail, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = _Owner(detail)
    target = cast(FleetPlanTarget, detail.run.target)
    repository_id = detail.run.targets[0].repository_id
    assert (
        _invoke(
            owner,
            "show",
            fleet_run_id=None,
            last=True,
            repository_id=repository_id,
            plan_id=target.plan.plan_id,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["fleet_run_id"] == detail.run.fleet_run_id
    assert owner.history_calls == [
        {"limit": 1, "repository_id": repository_id, "plan_id": target.plan.plan_id}
    ]
    assert owner.reads == [detail.run.fleet_run_id]


def test_empty_history_is_successful_read_with_no_invented_latest_run(
    detail: FleetRunDetail, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = _Owner(detail)
    owner.page = FleetRunHistoryPage(journal_revision=12, items=())
    assert _invoke(owner, "list") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["items"] == []
    assert result["next_before_revision"] is None
    assert owner.reads == []


@pytest.mark.parametrize(
    "repository_id,plan_id", [(None, None), ("repository", None), (None, "plan")]
)
def test_last_without_exact_scope_refuses_before_history(
    detail: FleetRunDetail,
    capsys: pytest.CaptureFixture[str],
    repository_id: str | None,
    plan_id: str | None,
) -> None:
    owner = _Owner(detail)
    assert (
        _invoke(
            owner,
            "show",
            fleet_run_id=None,
            last=True,
            repository_id=repository_id,
            plan_id=plan_id,
        )
        == 2
    )
    refused = json.loads(capsys.readouterr().out)
    assert refused["code"] == "INVALID_REQUEST"
    assert "--repository-id and --plan-id" in refused["safe_next_action"]
    assert owner.history_calls == []
    assert owner.reads == []


def test_last_empty_scope_does_not_fall_back_to_any_other_run(
    detail: FleetRunDetail, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = _Owner(detail)
    owner.page = FleetRunHistoryPage(journal_revision=12, items=())
    assert (
        _invoke(owner, "show", fleet_run_id=None, last=True, repository_id="repo", plan_id="plan")
        == 3
    )
    assert json.loads(capsys.readouterr().out)["code"] == "RUN_NOT_FOUND"
    assert owner.reads == []


def test_explain_keeps_original_failure_guidance_and_settlement(
    detail: FleetRunDetail, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = _Owner(detail)
    original = detail.game_tape
    assert original.why_not is not None
    assert _invoke(owner, "explain") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["why_not"] == original.why_not.model_dump(mode="json")
    assert result["summary"] == original.summary.model_dump(mode="json")
    assert result["status"] == detail.run.status.value
    assert result["outcome"] == detail.outcome.value
    assert result["source_admissions"][0]["repository_id"] == detail.run.targets[0].repository_id
    assert owner.reads == [detail.run.fleet_run_id]


def test_logs_json_keeps_owner_omissions_and_cursor(
    detail: FleetRunDetail, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = _Owner(detail)
    batch = _batch("SEALED", 9, dropped=8)
    owner.batches = [batch]
    assert _invoke(owner, "logs", after=4) == 0
    assert json.loads(capsys.readouterr().out) == batch.model_dump(mode="json")
    assert owner.cursors == [4]


def test_follow_preserves_original_cursors_and_stops_when_owner_seals(
    detail: FleetRunDetail,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Owner(detail)
    owner.batches = [_batch("ACTIVE", 1), _batch("ACTIVE", 2), _batch("SEALED", 3)]
    monkeypatch.setattr("oh_goon.cli_plan_run_readback.time.sleep", lambda _: None)
    assert _invoke(owner, "logs", follow=True, as_json=False) == 0
    output = capsys.readouterr().out
    for sequence in (1, 2, 3):
        assert output.count(f"recorded chunk {sequence}") == 1
    assert "state: SEALED" in output
    assert owner.cursors == [0, 1, 2]
    assert owner.reads == []


def test_follow_unavailable_does_not_reconnect_or_sleep(
    detail: FleetRunDetail,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Owner(detail)
    owner.batches = [_batch("UNAVAILABLE", 0)]

    def no_poll(_duration: float) -> None:
        pytest.fail("Unavailable owner must not be polled or reconnected")

    monkeypatch.setattr("oh_goon.cli_plan_run_readback.time.sleep", no_poll)
    assert _invoke(owner, "logs", follow=True, as_json=False) == 3
    assert "Transient output unavailable" in capsys.readouterr().out
    assert owner.cursors == [0]


@pytest.mark.parametrize("terminated", [False, True])
def test_follow_interrupt_is_read_only_and_returns_shell_interrupt_exit(
    detail: FleetRunDetail,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    terminated: bool,
) -> None:
    owner = _Owner(detail)
    owner.batches = [_batch("ACTIVE", 1)]

    def interrupt(_duration: float) -> None:
        if terminated:
            raise CliTermination
        raise KeyboardInterrupt

    monkeypatch.setattr("oh_goon.cli_plan_run_readback.time.sleep", interrupt)
    assert _invoke(owner, "logs", follow=True, as_json=False) == (143 if terminated else 130)
    assert "requested no Run cancellation" in capsys.readouterr().err
    assert owner.cursors == [0]


@pytest.mark.parametrize("last", [False, True])
def test_json_follow_refuses_before_any_live_or_durable_read(
    detail: FleetRunDetail, capsys: pytest.CaptureFixture[str], last: bool
) -> None:
    owner = _Owner(detail)
    assert (
        _invoke(
            owner,
            "logs",
            follow=True,
            last=last,
            fleet_run_id=None if last else detail.run.fleet_run_id,
            repository_id="repository" if last else None,
            plan_id="plan" if last else None,
        )
        == 2
    )
    assert "bounded JSON snapshots" in json.loads(capsys.readouterr().out)["safe_next_action"]
    assert owner.cursors == []
    assert owner.reads == []
    assert owner.history_calls == []


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("io_failure", [False, True])
def test_readback_errors_never_disclose_private_exception_text_or_notes(
    detail: FleetRunDetail,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
    io_failure: bool,
) -> None:
    owner = _Owner(detail)
    canary = "PRIVATE_READBACK_FAILURE_CANARY"
    error = OSError(canary) if io_failure else InvalidRequest(canary)
    error.add_note(canary + "_note")

    def reject(_run_id: str) -> FleetRunDetail:
        raise error

    monkeypatch.setattr(owner, "get", reject)
    assert _invoke(owner, "show", as_json=as_json) == (3 if io_failure else 2)
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err
    if as_json:
        result = json.loads(captured.out)
        assert result["fleet_run_id"] == detail.run.fleet_run_id
        assert result["code"] == ("RUN_READ_UNAVAILABLE" if io_failure else "INVALID_REQUEST")
    else:
        assert "Run readback refused" in captured.out


@pytest.mark.parametrize("io_failure", [False, True])
def test_main_json_keeps_readback_failure_private_and_preserves_exit_class(
    detail: FleetRunDetail,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    io_failure: bool,
) -> None:
    from oh_goon.cli import main

    owner = _Owner(detail)
    canary = "PRIVATE_MAIN_READBACK_FAILURE_CANARY"
    error = OSError(canary) if io_failure else InvalidRequest(canary)
    error.add_note(canary + "_note")

    def reject(_run_id: str) -> FleetRunDetail:
        raise error

    def shutdown(*, wait_for_runs: bool) -> None:
        assert wait_for_runs is False

    runtime = cast(ApplicationRuntime, SimpleNamespace(runs=owner, shutdown=shutdown))

    def build(*, startup_purpose: str = "application", **_options: object) -> ApplicationRuntime:
        assert startup_purpose == "observation"
        return runtime

    monkeypatch.setattr(owner, "get", reject)
    monkeypatch.setattr(ApplicationRuntime, "build", build)
    assert main(["runs", "show", detail.run.fleet_run_id, "--json"]) == (3 if io_failure else 2)
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err
    result = json.loads(captured.out)
    assert result["fleet_run_id"] == detail.run.fleet_run_id
    assert result["_cli"]["exit_kind"] == (
        "EVIDENCE_UNAVAILABLE" if io_failure else "COMMAND_ERROR"
    )
