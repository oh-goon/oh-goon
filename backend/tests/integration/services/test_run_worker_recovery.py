"""Real Run-worker custody across control loss, crashes and SQLite authority loss."""

from __future__ import annotations

import os
import select
import shlex
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
import yaml

from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon._application_worker import capture_run_worker_submission
from oh_goon.core.errors import ExecutionBusy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.process_birth import observe_process_birth, observe_process_liveness
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.vocabulary import (
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionLease
from oh_goon.runtime.journal import Journal
from oh_goon.services import effect_recovery as effect_recovery_owner
from tests.factories.capabilities import capability_snapshot

pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(
        not atomic_execution_bytes_supported()
        or not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal"),
        reason="native Linux sealed transport and birth-bound pidfds",
    ),
]

_CONTROLLER = """from pathlib import Path
import sys, time
from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.repository import RepositoryIdentity

root = Path(sys.argv[1])
repository = root / 'repos' / 'subject'
runtime = ApplicationRuntime.build(standard_local=True, startup_purpose='workflow')
try:
    runtime.locations.add(repository.parent)
    source = root / 'workflow' / 'goon.yml'
    runtime.plans.lock(source)
    target = runtime.plans.prepare_execution_target(source, repository=repository)
    run = runtime.runs.start_prepared_plan(
        target,
        repository_ids={RepositoryIdentity.model_validate({'path': repository}).repository_id},
        fleet_run_id='recovery-native-run',
    )
    try:
        runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=50)
    except Exception as error:
        # This is a test observation, never a Run result or recovery authority.
        (root / 'controller-observation').write_text(type(error).__name__)
        deadline = time.monotonic() + 45
        while not (root / 'controller-release').exists():
            if time.monotonic() > deadline:
                raise TimeoutError('test controller release unavailable')
            time.sleep(0.01)
finally:
    runtime.shutdown(wait_for_runs=True)
"""

_WORK = """from pathlib import Path
import os, sys, time

root = Path(sys.argv[1])
with (root / 'started').open('a') as stream:
    stream.write('started\\n')
(root / 'step-pid').write_text(str(os.getpid()))
(root / 'ready').touch()
deadline = time.monotonic() + 45
while not (root / 'release').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('test Step release unavailable')
    time.sleep(0.01)
with (root / 'external-effect').open('a') as stream:
    stream.write('applied\\n')
    stream.flush()
    os.fsync(stream.fileno())
(root / 'visible').touch()
print('effect applied', flush=True)
"""


def _wait_file(path: Path, *, controller: subprocess.Popen[bytes] | None = None) -> None:
    deadline = monotonic() + 20
    while not path.exists():
        if controller is not None and controller.poll() is not None:
            pytest.fail("native test controller exited before its controlled boundary")
        if monotonic() >= deadline:
            pytest.fail(f"native process did not reach {path.name}")
        Event().wait(0.01)


def _wait_pidfd(descriptor: int, *, timeout: float = 15) -> None:
    waiter = select.poll()
    waiter.register(descriptor, select.POLLIN)
    assert waiter.poll(int(timeout * 1000)), "owned native process did not exit"


def _pidfd_exited(descriptor: int) -> bool:
    waiter = select.poll()
    waiter.register(descriptor, select.POLLIN)
    return bool(waiter.poll(0))


@dataclass(frozen=True)
class _NativeRun:
    root: Path
    controller: subprocess.Popen[bytes]
    database: LocalDatabase
    lease: ExecutionLease
    worker_pidfd: int
    step_pidfd: int

    @property
    def run_id(self) -> str:
        return "recovery-native-run"

    def detail(self) -> FleetRunDetail:
        return Journal(self.database).read_view().required_run_detail(self.run_id)

    def kill_worker(self) -> None:
        signal.pidfd_send_signal(self.worker_pidfd, signal.SIGKILL)
        _wait_pidfd(self.worker_pidfd)


@contextmanager
def _native_run(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, journal_mode: str = "DELETE"
) -> Iterator[_NativeRun]:
    repository = root / "repos" / "subject"
    repository.mkdir(parents=True)
    (repository / "work.py").write_text(_WORK)
    for args in (
        ("init", "-q", str(repository)),
        ("-C", str(repository), "add", "work.py"),
        (
            "-C",
            str(repository),
            "-c",
            "user.name=Native qualification",
            "-c",
            "user.email=native@example.invalid",
            "commit",
            "-q",
            "-m",
            "subject",
        ),
    ):
        subprocess.run(("git", *args), check=True, capture_output=True, timeout=10)
    workflow = root / "workflow"
    workflow.mkdir()
    (workflow / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "execution": {"purpose": "verification"},
                "steps": [
                    {
                        "id": "external",
                        "run": f"{shlex.quote(sys.executable)} work.py {shlex.quote(str(root))}",
                    }
                ],
            }
        )
    )
    controller_source = root / "controller.py"
    controller_source.write_text(_CONTROLLER)
    database_path = root / "state" / "runtime.db"
    for name, value in (
        ("OH_GOON_DB", database_path),
        ("OH_GOON_LEAGUE_FILE", root / "league.yml"),
        ("XDG_CONFIG_HOME", root / "config"),
        ("XDG_DATA_HOME", root / "data"),
        ("XDG_STATE_HOME", root / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, journal_mode)
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    worker_pidfd: int | None = None
    step_pidfd: int | None = None
    with (root / "controller.log").open("wb") as output:
        controller = subprocess.Popen(
            (sys.executable, "-I", str(controller_source), str(root)),
            env=dict(os.environ),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        _wait_file(root / "ready", controller=controller)
        database = LocalDatabase(database_path)
        lease = ExecutionCoordinator(database).active_lease()
        assert lease is not None and lease.custody is not None
        assert lease.custody.phase == "WORKER_OWNED"
        assert lease.custody.owner_pid not in {os.getpid(), controller.pid}
        birth = observe_process_birth(lease.custody.owner_pid)
        assert birth is not None
        worker_pidfd = os.pidfd_open(lease.custody.owner_pid)
        assert observe_process_birth(lease.custody.owner_pid) == birth
        step_pid = int((root / "step-pid").read_text())
        step_birth = observe_process_birth(step_pid)
        assert step_birth is not None
        step_pidfd = os.pidfd_open(step_pid)
        assert observe_process_birth(step_pid) == step_birth
        yield _NativeRun(root, controller, database, lease, worker_pidfd, step_pidfd)
    finally:
        if worker_pidfd is None or not _pidfd_exited(worker_pidfd):
            (root / "release").touch()
        elif step_pidfd is not None:
            # A failed test must not authorize a late external effect merely
            # to release a Step whose actual execution owner has already died.
            try:
                signal.pidfd_send_signal(step_pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        (root / "controller-release").touch()
        if controller.poll() is None:
            try:
                controller.wait(timeout=10)
            except subprocess.TimeoutExpired:
                controller.kill()
                controller.wait(timeout=5)
        for descriptor in (worker_pidfd, step_pidfd):
            if descriptor is not None:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    _wait_pidfd(descriptor, timeout=5)
                except ProcessLookupError:
                    pass
                finally:
                    os.close(descriptor)


def _replacement() -> RuntimeFoundation:
    return build_runtime_foundation(standard_local=True, startup_purpose="application")


@pytest.mark.parametrize("captured_repository", (False, True), ids=("empty-capture", "one-subject"))
def test_live_claim_before_start_cannot_be_recovered_until_native_owner_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_repository: bool
) -> None:
    for name, value in (
        ("OH_GOON_DB", tmp_path / "state" / "runtime.db"),
        ("OH_GOON_LEAGUE_FILE", tmp_path / "league.yml"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    foundation = build_runtime_foundation(standard_local=True, startup_purpose="workflow")
    repository = tmp_path / "repos" / "subject"
    repository.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", str(repository)), check=True, timeout=10)
    foundation.state.add_location(repository.parent)
    identity = RepositoryIdentity.model_validate({"path": repository})
    captured = [identity] if captured_repository else []
    run_id = "claimed-before-start"
    prepare = capture_run_worker_submission(foundation, run_id)
    intent = FleetRunAdmissionIntent(
        capability=capability_snapshot(OperationAction.INIT),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    ticket, _ = foundation.execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=foundation.database.durable_revision(),
        mutate=lambda connection: foundation.state.journal.admit_run_in(
            connection,
            run_id,
            intent,
            fleet_repository_scope({identity.repository_id} if captured_repository else None),
            captured,
        ),
    )
    launch = prepare(ticket)
    # Stop at the actual canonical claim boundary. This fixture intentionally
    # performs no FleetRunExecution or Step work before the controlled death.
    source = (
        "from pathlib import Path\n"
        "import sys,time\n"
        "from oh_goon._application_worker import _restore_handoff\n"
        "from oh_goon.runtime.database import LocalDatabase\n"
        "from oh_goon.runtime.execution import ExecutionCoordinator,ExecutionTicket\n"
        "from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime\n"
        "handoff=_restore_handoff(sys.argv[1])\n"
        "require_loaded_hashmarks_runtime(expected_implementation_identity=\n"
        " handoff.configuration.hashmarks_implementation_identity)\n"
        "database=LocalDatabase.open_worker(handoff.database)\n"
        "ExecutionCoordinator(database).claim_fleet_worker(\n"
        " ExecutionTicket.from_worker_handoff(handoff.ticket))\n"
        "root=Path(sys.argv[2])\n"
        "(root/'claimed').touch()\n"
        "deadline=time.monotonic()+30\n"
        "while not (root/'claim-release').exists():\n"
        " if time.monotonic()>deadline: raise TimeoutError('controlled release unavailable')\n"
        " time.sleep(0.01)\n"
        "raise SystemExit(37)\n"
    )
    launch = replace(
        launch, argv=(sys.executable, "-I", "-c", source, launch.argv[-1], str(tmp_path))
    )
    try:
        foundation.supervisor.submit(run_id, launch)
        _wait_file(tmp_path / "claimed")
        live_lease = foundation.execution.active_lease()
        assert live_lease is not None and live_lease.custody is not None
        assert live_lease.custody.phase == "WORKER_OWNED"
        assert observe_process_liveness(live_lease.custody.owner_pid) is True
        live_replacement = _replacement()
        try:
            assert live_replacement.execution.active_lease() == live_lease
            detail = live_replacement.state.journal.read_view().required_run_detail(run_id)
            assert detail.run.status is FleetRunStatus.QUEUED
            assert detail.operations == ()
            events = live_replacement.state.journal.read_view().run_events(run_id).events
            assert all(event.event_type is not FleetRunEventType.RUN_STARTED for event in events)
        finally:
            live_replacement.supervisor.shutdown(wait=True)
            live_replacement.lifecycle.close()
        (tmp_path / "claim-release").touch()
        assert foundation.supervisor.wait_until_inactive(run_id, timeout=15)
        assert observe_process_liveness(live_lease.custody.owner_pid) is False
        assert (
            foundation.state.journal.read_view().required_run_detail(run_id).run.status
            is FleetRunStatus.QUEUED
        )
        runtime = build_product_services(foundation)
        try:
            runtime.recovery.recover_worker_exit(run_id)
            detail = foundation.state.journal.read_view().required_run_detail(run_id)
            assert detail.run.status is FleetRunStatus.FAILED
            assert detail.run.failure.code == "RUN_INTERRUPTED_BY_RESTART"
            assert detail.operations == ()
            assert foundation.execution.active_lease() is None
            events = foundation.state.journal.read_view().run_events(run_id).events
            assert all(event.event_type is not FleetRunEventType.RUN_STARTED for event in events)
            revision = foundation.database.durable_revision()
            foundation.effect_recovery.recover(fleet_run_id=run_id)
            assert foundation.database.durable_revision() == revision
        finally:
            runtime.shutdown(wait_for_runs=True)
    finally:
        (tmp_path / "claim-release").touch()
        foundation.supervisor.shutdown(wait=True)
        if foundation.execution.active_lease() is not None:
            foundation.execution.activate_submission_failure(ticket).finish(
                lambda connection: foundation.state.journal.terminalize_run_in(
                    connection, run_id, FleetRunTerminalIntent.CANCEL
                )
            )
        if launch.cleanup is not None:
            launch.cleanup()
        foundation.lifecycle.close()


@pytest.mark.parametrize("journal_mode", ("DELETE", "WAL"))
def test_parent_death_does_not_steal_or_redispatch_a_live_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, journal_mode: str
) -> None:
    with _native_run(tmp_path, monkeypatch, journal_mode=journal_mode) as run:
        assert run.detail().run.status is FleetRunStatus.RUNNING
        run.controller.kill()
        run.controller.wait(timeout=10)
        assert observe_process_liveness(run.controller.pid) is False
        assert run.lease.custody is not None
        assert observe_process_liveness(run.lease.custody.owner_pid) is True

        replacement = _replacement()
        runtime = build_product_services(replacement)
        try:
            before = replacement.database.durable_revision()
            replacement.effect_recovery.recover()
            assert replacement.database.durable_revision() == before
            assert replacement.execution.active_lease() == run.lease
            with pytest.raises(ExecutionBusy):
                runtime.recovery.recover_worker_exit(run.run_id)
            assert replacement.database.durable_revision() == before
            detail = runtime.runs.get(run.run_id)
            assert detail.run.status is FleetRunStatus.RUNNING
            assert isinstance(detail.run.target, FleetPlanTarget)
            replay = runtime.runs.start_prepared_plan(
                detail.run.target,
                repository_ids={detail.run.targets[0].repository_id},
                fleet_run_id=run.run_id,
            )
            assert replay == detail.run
            with pytest.raises(ExecutionBusy):
                runtime.runs.start_prepared_plan(
                    detail.run.target,
                    repository_ids={detail.run.targets[0].repository_id},
                    fleet_run_id="must-not-steal-worker",
                )
            assert replacement.execution.active_lease() == run.lease
            assert (tmp_path / "started").read_text() == "started\n"
            (tmp_path / "release").touch()
            _wait_pidfd(run.worker_pidfd)
            completed = runtime.runs.get(run.run_id)
            assert completed.run.status is FleetRunStatus.COMPLETED
            assert (tmp_path / "external-effect").read_text() == "applied\n"
            assert replacement.execution.active_lease() is None
        finally:
            runtime.shutdown(wait_for_runs=True)


@pytest.mark.parametrize(
    "effect_visible", (False, True), ids=("after-start", "after-external-effect")
)
def test_dead_worker_recovery_preserves_unknown_effect_and_never_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effect_visible: bool
) -> None:
    with _native_run(tmp_path, monkeypatch) as run:
        attempts = RepositoryEffectAttemptStore(run.database)
        observed = attempts.all()
        assert len(observed) == 1
        assert observed[0].status is EffectAttemptStatus.EFFECT_STARTED
        assert observed[0].effect_boundary is EffectBoundaryKind.EXTERNAL
        before = run.database.durable_revision()
        live_recovery = _replacement()
        live_recovery.effect_recovery.recover()
        assert run.database.durable_revision() == before
        assert live_recovery.execution.active_lease() == run.lease
        live_recovery.supervisor.shutdown(wait=True)
        live_recovery.lifecycle.close()

        if effect_visible:
            # The real Step may finish its external effect, while the original
            # SQLite writer cut prevents any later settlement from committing.
            with run.database.transaction(write=True):
                (tmp_path / "release").touch()
                _wait_file(tmp_path / "visible")
                _wait_pidfd(run.step_pidfd)
                assert (tmp_path / "external-effect").read_text() == "applied\n"
                run.kill_worker()
        else:
            run.kill_worker()
        assert run.lease.custody is not None
        assert observe_process_liveness(run.lease.custody.owner_pid) is False
        _wait_file(tmp_path / "controller-observation", controller=run.controller)
        assert run.controller.poll() is None  # An abnormal worker exit is isolated.
        interrupted = run.detail()
        assert interrupted.run.status is FleetRunStatus.RUNNING
        assert interrupted.operations == ()

        recovered = build_runtime_foundation(standard_local=True, startup_purpose="workflow")
        runtime = build_product_services(recovered)
        try:
            revision = recovered.database.durable_revision()
            # This policy vector declares an unavailable native group probe;
            # it is not a claim that the already-stopped process is still live.
            with monkeypatch.context() as unavailable:
                unavailable.setattr(
                    effect_recovery_owner, "process_session_exists", lambda _pid: True
                )
                recovered.effect_recovery.recover()
                assert recovered.database.durable_revision() == revision
                with pytest.raises(ExecutionBusy):
                    runtime.recovery.recover_worker_exit(run.run_id)
            assert recovered.database.durable_revision() == revision
            assert run.detail().run.status is FleetRunStatus.RUNNING
            assert RepositoryEffectAttemptStore(recovered.database).all() == observed
            runtime.recovery.recover_worker_exit(run.run_id, quiescence_timeout_seconds=5)
            detail = runtime.runs.get(run.run_id)
            assert detail.run.status is FleetRunStatus.FAILED
            assert detail.run.failure.code == "REPOSITORY_EFFECT_OUTCOME_UNKNOWN"
            assert detail.operations == ()
            assert recovered.execution.active_lease() is None
            quarantined = RepositoryEffectAttemptStore(recovered.database).all()
            assert len(quarantined) == 1
            assert quarantined[0].status is EffectAttemptStatus.INDETERMINATE
            assert quarantined[0].quarantines_repository
            assert runtime.recovery.status().effect_quarantines
            with monkeypatch.context() as unavailable:
                unavailable.setattr(
                    effect_recovery_owner,
                    "process_session_exists",
                    lambda _pid: pytest.fail("Original quiescence receipt must be reused"),
                )
                acknowledged = runtime.recovery.acknowledge(
                    quarantined[0].operation_id,
                    note="Inspected external state after native containment",
                    expected_record_version=quarantined[0].record_version,
                )
            assert acknowledged.effect_quarantines == ()
            assert runtime.runs.get(run.run_id).run.status is FleetRunStatus.FAILED
            assert runtime.runs.get(run.run_id).operations == ()
            events = recovered.state.journal.read_view().run_events(run.run_id).events
            assert sum(event.event_type is FleetRunEventType.RUN_STARTED for event in events) == 1
            assert any(
                event.event_type is FleetRunEventType.PROCESS_STATE_UNKNOWN for event in events
            )
            revision = recovered.database.durable_revision()
            recovered.effect_recovery.recover()
            assert recovered.database.durable_revision() == revision
            assert (tmp_path / "started").read_text() == "started\n"
            if effect_visible:
                assert (tmp_path / "external-effect").read_text() == "applied\n"
        finally:
            runtime.shutdown(wait_for_runs=True)


def test_worker_database_authority_loss_never_creates_or_terminalizes_a_shadow_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _native_run(tmp_path, monkeypatch) as run:
        retained = run.database.path.with_name("retained-authority.db")
        run.database.path.replace(retained)
        try:
            (tmp_path / "release").touch()
            _wait_pidfd(run.worker_pidfd)
            _wait_file(tmp_path / "controller-observation", controller=run.controller)
            assert not run.database.path.exists()
            with pytest.raises(DatabasePathIdentityLost):
                run.detail()
            forensic = Journal(LocalDatabase(retained, read_only=True)).read_view()
            detail = forensic.required_run_detail(run.run_id)
            assert detail.run.status is FleetRunStatus.RUNNING
            assert detail.operations == ()
            assert not detail.run.status.terminal
            assert not run.database.path.exists()
        finally:
            retained.replace(run.database.path)
