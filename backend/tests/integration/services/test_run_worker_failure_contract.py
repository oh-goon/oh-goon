"""Native bootstrap failures retain distinct safe observations and canonical Run truth."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon._application_worker import capture_run_worker_submission
from oh_goon.core.models.background import RunWorkerExitCode
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import FleetRunStatus, FleetRunTerminalIntent, OperationAction
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.runtime.execution import ExecutionTicket
from tests.factories.capabilities import capability_snapshot

_FAILURE = """import sys
from oh_goon import _application_worker as worker
from oh_goon.core.errors import OperationCancelled
from oh_goon.runtime.database import DatabasePathIdentityLost
from oh_goon.services.fleet_run_execution import FleetRunExecution

cut = sys.argv[2]
private = {}
restore = worker._restore_handoff
def capture(header):
    handoff = restore(header)
    private['claim'] = handoff.ticket['lease_token']
    return handoff
worker._restore_handoff = capture
def fail(*args, **kwargs):
    raise ValueError('protected-failure-canary ' + private['claim'])
if cut == 'identity':
    worker.require_loaded_hashmarks_runtime = fail
elif cut == 'database':
    def unavailable(*args, **kwargs):
        raise DatabasePathIdentityLost('protected-failure-canary ' + private['claim'])
    worker.LocalDatabase.open_worker = classmethod(unavailable)
elif cut == 'claim':
    worker.ExecutionCoordinator.claim_fleet_worker = fail
elif cut == 'bootstrap':
    worker.build_claimed_run_execution = fail
elif cut == 'execution':
    def execution(self, session):
        raise ValueError('controlled execution failure')
    FleetRunExecution._run = execution
elif cut == 'cancellation':
    def cancel(self, session):
        self._lifecycle.cancel(session.require_fleet_run_id())
        raise OperationCancelled('controlled cancellation')
    FleetRunExecution._run = cancel
elif cut == 'cleanup':
    worker.WorkerRunObservations.close = fail
raise SystemExit(worker.execute_handoff(sys.argv[1]))
"""


@pytest.fixture
def foundation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RuntimeFoundation]:
    for name, selected_path in (
        ("OH_GOON_DB", tmp_path / "state" / "runtime.db"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(selected_path))
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    value = build_runtime_foundation(standard_local=True, startup_purpose="workflow")
    try:
        yield value
    finally:
        value.supervisor.shutdown(wait=True)
        value.lifecycle.close()


def _admit(foundation: RuntimeFoundation, root: Path) -> ExecutionTicket:
    repository = root / "repos" / "subject"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "pyproject.toml").write_text("[project]\nname='subject'\nversion='0.1.0'\n")
    foundation.state.add_location(repository.parent)
    identity = RepositoryIdentity.model_validate({"path": repository})
    ticket, _ = foundation.execution.reserve_fleet_with(
        "failure-contract-run",
        expected_durable_revision=foundation.database.durable_revision(),
        mutate=lambda connection: foundation.state.journal.admit_run_in(
            connection,
            "failure-contract-run",
            FleetRunAdmissionIntent(
                capability=capability_snapshot(OperationAction.INIT),
                execution=DirectFleetExecutionAdmission(),
                repository_admissions=(),
            ),
            fleet_repository_scope({identity.repository_id}),
            [identity],
        ),
    )
    return ticket


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native sealed FD transport")
@pytest.mark.parametrize(
    "cut,code,status",
    (
        ("identity", RunWorkerExitCode.RUNTIME_IDENTITY_REFUSED, FleetRunStatus.QUEUED),
        ("database", RunWorkerExitCode.DATABASE_UNAVAILABLE, FleetRunStatus.QUEUED),
        ("claim", RunWorkerExitCode.CLAIM_REFUSED, FleetRunStatus.QUEUED),
        ("bootstrap", RunWorkerExitCode.BOOTSTRAP_FAILED, FleetRunStatus.QUEUED),
        ("execution", RunWorkerExitCode.EXECUTION_FAILED, FleetRunStatus.FAILED),
        ("cancellation", RunWorkerExitCode.CANCELLATION_OBSERVED, FleetRunStatus.CANCELLED),
        ("cleanup", 0, FleetRunStatus.COMPLETED),
    ),
)
def test_native_failure_cut_is_diagnostic_without_claim_disclosure_or_result_override(
    foundation: RuntimeFoundation,
    tmp_path: Path,
    cut: str,
    code: int,
    status: FleetRunStatus,
) -> None:
    prepare = capture_run_worker_submission(foundation, "failure-contract-run")
    ticket = _admit(foundation, tmp_path)
    launch = prepare(ticket)
    observed: list[int | None] = []
    output: list[str] = []
    secret = ticket.export_worker_handoff()["lease_token"]
    assert isinstance(secret, str)
    launch = replace(
        launch,
        argv=(sys.executable, "-I", "-c", _FAILURE, launch.argv[-1], cut),
        on_exit=observed.append,
        stream_output=lambda _stream, text: output.append(text),
    )
    try:
        foundation.supervisor.submit("failure-contract-run", launch)
        assert foundation.supervisor.wait_until_inactive("failure-contract-run", timeout=20)
        assert observed == [code]
        detail = foundation.state.journal.read_view().required_run_detail("failure-contract-run")
        assert detail.run.status is status
        assert secret not in " ".join(launch.argv)
        assert secret not in "".join(output) and "protected-failure-canary" not in "".join(output)
        failure = foundation.supervisor.take_failure("failure-contract-run")
        if code:
            assert failure is not None
            assert secret not in failure.model_dump_json()
            assert "protected-failure-canary" not in failure.model_dump_json()
        else:
            assert failure is None
            assert "observation cleanup incomplete" in "".join(output)
        with foundation.database.transaction() as connection:
            row = connection.execute("SELECT custody_phase FROM execution_lease").fetchone()
        if cut in {"identity", "database", "claim"}:
            assert row is not None and row[0] == "ADMITTED_UNCLAIMED"
        elif cut == "bootstrap":
            assert row is not None and row[0] == "WORKER_OWNED"
        else:
            assert row is None
    finally:
        if cut in {"identity", "database", "claim"}:
            foundation.execution.activate_submission_failure(ticket).finish(
                lambda connection: foundation.state.journal.terminalize_run_in(
                    connection, "failure-contract-run", FleetRunTerminalIntent.CANCEL
                )
            )
        if launch.cleanup is not None:
            launch.cleanup()
