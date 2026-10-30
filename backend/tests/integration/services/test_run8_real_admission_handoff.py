"""RUN-8A: real Fleet SQLite lifecycle and agent readback, no native claims.

Operation INIT is used because the Plan source-snapshot authority depends on the
external Hashmarks wheel, unavailable on this host. The test is deliberately NOT
Plan admission proof or physical LACE certification. Do not substitute an
invented filesystem digest or software custody permit.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import FleetRunOutcome, OperationAction, Verdict
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import RunExecutionSupervisor
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import fleet_stack


class _ResultOperation:
    def __init__(self, *, failed: bool) -> None:
        self.failed = failed

    def run(self, repository: Path, action: OperationAction, **kwargs):
        assert action is OperationAction.INIT
        return OperationExecution(
            stdout="",
            stderr="",
            report=make_operation_report(
                repository,
                action,
                verdict=Verdict.FAILED if self.failed else Verdict.READY,
                reason_code=ReasonCode.OPERATION_FAILED if self.failed else ReasonCode.READY,
                fleet_run_id=kwargs["fleet_run_id"],
            ),
        )


def _read(runtime, capsys, *argv: str) -> dict:
    parsed = build_parser().parse_args(("runs", *argv, "--json"))
    assert run_plan_command(runtime, parsed) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("failed", [False, True])
def test_original_fleet_sqlite_terminal_handoff_restarts_without_hashmarks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failed: bool,
) -> None:
    root = tmp_path / "repos"
    repository = root / "sample"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='sample'\nversion='0.1.0'\n")
    database_path = tmp_path / "runtime.db"
    state = local_state(LocalDatabase(database_path))
    state.add_location(root)
    _, owner, _, _ = fleet_stack(state, _ResultOperation(failed=failed))
    repository_id = RepositoryIdentity(path=repository).repository_id
    run_id = f"run8a-operation-{int(failed)}"
    try:
        admitted = owner.start(
            OperationAction.INIT, repository_ids={repository_id}, fleet_run_id=run_id
        )
        assert admitted.fleet_run_id == run_id
        detail = owner.wait_for_terminal(run_id, timeout=10)
        assert (detail.outcome is FleetRunOutcome.SUCCESS) is (not failed)
    finally:
        owner.shutdown()
    # Actual new SQLite/Journal, original read-only control plane; no in-memory
    # continuation and no reinterpretation of terminal status.
    reader = FleetRunControlPlane(
        admission=None,  # type: ignore[arg-type]
        journal=Journal(LocalDatabase(database_path)),
        lifecycle=None,  # type: ignore[arg-type]
        run_execution=None,  # type: ignore[arg-type]
        supervisor=RunExecutionSupervisor(),
        live_output=GameTapeLiveOutputStore(),
    )
    runtime = SimpleNamespace(runs=reader)
    status = _read(runtime, capsys, "show", run_id)
    handoff = _read(runtime, capsys, "handoff", run_id)
    tape = _read(runtime, capsys, "tape", run_id)
    steps = _read(runtime, capsys, "steps", run_id)
    outputs = _read(runtime, capsys, "outputs", run_id)
    logs = _read(runtime, capsys, "logs", run_id, "--after", "0")
    assert all(
        data["fleet_run_id"] == run_id for data in (status, handoff, tape, steps, outputs, logs)
    )
    assert handoff["outcome"] == detail.outcome.value
    assert (handoff["outcome"] == "SUCCESS") is (not failed)
    assert handoff["readback"]
    assert "retry" not in handoff["readback"]
    if failed:
        assert handoff["outcome"] != "SUCCESS"
    # RUN-3 cursor resumption, backed by durable Journal, not CLI slicing.
    batch = _read(runtime, capsys, "events", run_id, "--limit", "1", "--after", "0")
    seen = list(batch["events"])
    while batch["has_more"]:
        cursor = batch["next_sequence"]
        batch = _read(runtime, capsys, "events", run_id, "--limit", "2", "--after", str(cursor))
        assert batch["events"]
        seen.extend(batch["events"])
        assert batch["next_sequence"] > cursor
    durable = reader.events(run_id).events
    assert [event["sequence"] for event in seen] == [event.sequence for event in durable]
    assert batch["next_sequence"] == durable[-1].sequence
    assert len(seen) > 1
