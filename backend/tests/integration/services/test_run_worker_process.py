"""Native claimed-worker bootstrap, using the existing non-Plan Fleet lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon._application_worker import capture_run_worker_submission
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunFailureStage,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
    ResultFreshness,
)
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.runtime.execution import ExecutionTicket
from oh_goon.runtime.execution.run_supervisor import RunWorkerLaunch
from tests.factories.capabilities import capability_snapshot


@pytest.fixture
def foundation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimeFoundation:
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "runtime.db"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    return build_runtime_foundation(standard_local=True, startup_purpose="workflow")


def _admit_repository(
    foundation: RuntimeFoundation, tmp_path: Path, run_id: str
) -> ExecutionTicket:
    repository = tmp_path / "repos" / "subject"
    repository.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", str(repository)], check=True, capture_output=True, timeout=10
    )
    (repository / "pyproject.toml").write_text("[project]\nname='subject'\nversion='0.1.0'\n")
    foundation.state.add_location(repository.parent)
    target = RepositoryIdentity.model_validate({"path": repository})
    scope = fleet_repository_scope({target.repository_id})
    intent = FleetRunAdmissionIntent(
        capability=capability_snapshot(OperationAction.INIT),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    ticket, _ = foundation.execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=foundation.database.durable_revision(),
        mutate=lambda connection: foundation.state.journal.admit_run_in(
            connection, run_id, intent, scope, [target]
        ),
    )
    return ticket


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native sealed FD transport")
@pytest.mark.parametrize("transport", ("valid", "changed-identity", "closed-descriptor"))
def test_native_worker_requires_the_exact_protected_admission_before_effects(
    foundation: RuntimeFoundation, tmp_path: Path, transport: str
) -> None:
    run_id = "protected-run"
    prepare = capture_run_worker_submission(foundation, run_id)
    ticket = _admit_repository(foundation, tmp_path, run_id)
    launch = prepare(ticket)
    assert ticket._lease.token not in " ".join(launch.argv)
    if transport == "changed-identity":
        header = json.loads(launch.argv[-1])
        header["identity"] = "sha256:" + "0" * 64
        launch = replace(launch, argv=(*launch.argv[:-1], json.dumps(header)))
    elif transport == "closed-descriptor":
        header = json.loads(launch.argv[-1])
        launch = replace(
            launch, pass_fds=tuple(fd for fd in launch.pass_fds if fd != header["descriptor"])
        )
    try:
        foundation.supervisor.submit(run_id, launch)
        assert foundation.supervisor.wait_until_inactive(run_id, timeout=20)
        events = foundation.state.journal.read_view().run_events(run_id).events
        if transport == "valid":
            detail = foundation.state.journal.read_view().required_run_detail(run_id)
            assert detail.run.status is FleetRunStatus.COMPLETED
            assert events[0].event_type is FleetRunEventType.RUN_ADMITTED
            assert any(event.event_type is FleetRunEventType.RUN_STARTED for event in events)
            assert events[-1].event_type is FleetRunEventType.RUN_COMPLETED
            assert foundation.execution.active_lease() is None
            assert foundation.supervisor.take_failure(run_id) is None
            report = detail.operations[0]
            current = foundation.state.journal.current_capability(
                report.repository.path, report.capability.capability_id
            )
            assert current is not None and current.freshness is ResultFreshness.CURRENT
            with foundation.database.transaction() as connection:
                writer = connection.execute(
                    "SELECT session_id FROM journal_entries WHERE kind='FLEET_OPERATION'"
                ).fetchone()
                assert writer is not None and writer[0] != foundation.database.session_id
        else:
            assert [event.event_type for event in events] == [FleetRunEventType.RUN_ADMITTED]
            detail = foundation.state.journal.read_view().required_run_detail(run_id)
            assert detail.run.status is FleetRunStatus.QUEUED
            lease = foundation.execution.active_lease()
            assert lease is not None and lease.custody is not None
            assert lease.custody.phase == "ADMITTED_UNCLAIMED"
            assert lease.custody.owner_pid == os.getpid()
            failure = foundation.supervisor.take_failure(run_id)
            assert failure is not None
            assert ticket._lease.token not in failure.model_dump_json()
    finally:
        foundation.supervisor.shutdown(wait=True)
        lease = foundation.execution.active_lease()
        if (
            lease is not None
            and lease.custody is not None
            and lease.custody.phase == "ADMITTED_UNCLAIMED"
        ):
            foundation.execution.activate_submission_failure(ticket).finish(
                lambda connection: foundation.state.journal.terminalize_run_in(
                    connection, run_id, FleetRunTerminalIntent.CANCEL
                )
            )
        if launch.cleanup is not None:
            launch.cleanup()


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native sealed FD transport")
@pytest.mark.parametrize("exit_cut", ("before-claim", "after-terminal", "after-claim"))
def test_control_plane_uses_durable_custody_and_terminal_truth_after_native_exit(
    foundation: RuntimeFoundation, tmp_path: Path, exit_cut: str
) -> None:
    run_id = "native-exit-run"
    prepare = capture_run_worker_submission(foundation, run_id)
    ticket = _admit_repository(foundation, tmp_path, run_id)
    runtime = build_product_services(foundation)

    def selected_launch(selected: ExecutionTicket) -> RunWorkerLaunch:
        launch = prepare(selected)
        if exit_cut == "before-claim":
            source = "raise SystemExit(7)"
        elif exit_cut == "after-terminal":
            source = (
                "import sys\n"
                "from oh_goon._application_worker import execute_handoff\n"
                "assert execute_handoff(sys.argv[1]) == 0\n"
                "raise SystemExit(23)\n"
            )
        else:
            source = (
                "import sys\n"
                "from oh_goon._application_worker import _restore_handoff\n"
                "from oh_goon.runtime.database import LocalDatabase\n"
                "from oh_goon.runtime.execution import ExecutionCoordinator,ExecutionTicket\n"
                "handoff = _restore_handoff(sys.argv[1])\n"
                "database = LocalDatabase.open_worker(handoff.database)\n"
                "ExecutionCoordinator(database).claim_fleet_worker(\n"
                " ExecutionTicket.from_worker_handoff(handoff.ticket))\n"
                "raise SystemExit(19)\n"
            )
        return replace(launch, argv=(sys.executable, "-I", "-c", source, launch.argv[-1]))

    try:
        runtime.runs._submit(run_id, ticket, selected_launch)
        if exit_cut == "after-claim":
            with pytest.raises(StateConflict, match="before durable terminal"):
                runtime.runs.wait_for_terminal(run_id, timeout=20)
            detail = foundation.state.journal.read_view().required_run_detail(run_id)
            assert detail.run.status is FleetRunStatus.QUEUED
            with foundation.database.transaction() as connection:
                custody = connection.execute(
                    "SELECT custody_phase, owner_pid FROM execution_lease WHERE singleton=1"
                ).fetchone()
                assert custody is not None
                assert custody[0] == "WORKER_OWNED"
                assert custody[1] != os.getpid()
        else:
            detail = runtime.runs.wait_for_terminal(run_id, timeout=20)
            if exit_cut == "before-claim":
                assert detail.run.status is FleetRunStatus.FAILED
                assert detail.run.failure is not None
                assert detail.run.failure.stage is FleetRunFailureStage.SUBMISSION
            else:
                assert detail.run.status is FleetRunStatus.COMPLETED
            assert foundation.execution.active_lease() is None
        events = foundation.state.journal.read_view().run_events(run_id).events
        if exit_cut != "after-terminal":
            assert all(event.event_type is not FleetRunEventType.RUN_STARTED for event in events)
        else:
            assert events[-1].event_type is FleetRunEventType.RUN_COMPLETED
    finally:
        runtime.shutdown(wait_for_runs=True)
