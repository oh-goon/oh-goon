from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import ErrorKind, RuntimeSafetyBlocked, StateConflict
from oh_goon.core.models.recovery import InvariantAudit, InvariantSeverity, PlainInvariantFinding
from oh_goon.core.models.runtime_safety import (
    DegradedRuntimeSafetyProof,
    ProvenRuntimeSafetyProof,
)
from oh_goon.core.models.tool_health import ReadyToolHealth
from oh_goon.core.vocabulary import OperationAction, RuntimeSafetyStatus, SystemHealthStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator, RunExecutionSupervisor
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.lifecycle import RuntimeLifecycle
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_scheduler import ScheduleScheduler
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.schedules import ScheduleService
from oh_goon.services.system_health import SystemHealthService
from tests.factories.runtime_stack import direct_operation_stack
from tests.fakes.capability_runners import ExternalRunnerStub


class _NeverAdapter(ExternalRunnerStub):
    def __init__(self) -> None:
        self.calls = 0

    def execute_request(self, _request):
        self.calls += 1
        raise AssertionError("adapter must not run while safety proof is degraded")


class _BlockedAdmission:
    def __init__(self, runtime_safety: RuntimeSafetyService) -> None:
        self._runtime_safety = runtime_safety

    def plan(self, *_args, **_kwargs):
        self._runtime_safety.admit_effects()
        raise AssertionError("admission planning must not continue while safety proof is degraded")


class _UnusedExecutor:
    pass


def _degraded_service(database: LocalDatabase | None = None) -> RuntimeSafetyService:
    revision = database.durable_revision() if database is not None else 0

    class Auditor:
        def current_durable_revision(self) -> int:
            return database.durable_revision() if database is not None else revision

        def audit(self, cut=None) -> InvariantAudit:
            revision = cut.durable_revision if cut is not None else self.current_durable_revision()
            return InvariantAudit(
                durable_revision=revision,
                findings=[
                    PlainInvariantFinding(
                        finding_type="PLAIN",
                        code="BROKEN_INVARIANT",
                        severity=InvariantSeverity.ERROR,
                        message="unsafe durable state",
                    )
                ],
            )

        def read_cut(self):
            from oh_goon.runtime.journal_view import JournalReadView
            from oh_goon.runtime.safety_cut import ConsistentRuntimeSafetyCut

            return ConsistentRuntimeSafetyCut(
                durable_revision=self.current_durable_revision(),
                journal=JournalReadView.from_entries((), "test"),
                active_lease=None,
                effect_attempts=(),
                active_schedules=(),
            )

    gate = RuntimeSafetyGate()
    service = RuntimeSafetyService(Auditor(), gate)  # type: ignore[arg-type]
    service.refresh()
    return service


def test_journal_corrupt_safety_cut_requires_integrity_error() -> None:
    from oh_goon.runtime.safety_cut import JournalCorruptRuntimeSafetyCut

    with pytest.raises(ValueError, match="requires an integrity error"):
        JournalCorruptRuntimeSafetyCut(
            durable_revision=0,
            active_lease=None,
            effect_attempts=(),
            active_schedules=(),
            journal_error="",
        )


def test_runtime_safety_gate_fails_closed_before_proof() -> None:
    gate = RuntimeSafetyGate()
    assert gate.proof().status is RuntimeSafetyStatus.UNPROVEN
    with pytest.raises(RuntimeSafetyBlocked, match="UNPROVEN"):
        gate.require_effects_allowed()


def test_runtime_safety_gate_allows_only_healthy_proof() -> None:
    gate = RuntimeSafetyGate()
    gate.record(
        InvariantAudit(
            durable_revision=0,
            findings=[
                PlainInvariantFinding(
                    finding_type="PLAIN",
                    code="BROKEN_INVARIANT",
                    severity=InvariantSeverity.ERROR,
                    message="unsafe durable state",
                )
            ],
        )
    )
    with pytest.raises(RuntimeSafetyBlocked, match="BROKEN_INVARIANT"):
        gate.require_effects_allowed()
    gate.record(InvariantAudit(durable_revision=0, journal_revision=0))
    gate.require_effects_allowed()
    assert gate.proof().status is RuntimeSafetyStatus.PROVEN_SAFE


def test_recorded_safety_is_always_assessed_and_admitted_cut_is_consistent() -> None:
    from oh_goon.runtime.journal_view import JournalReadView
    from oh_goon.runtime.safety_cut import ConsistentRuntimeSafetyCut

    gate = RuntimeSafetyGate()
    degraded = gate.record(
        InvariantAudit(
            durable_revision=0,
            findings=[
                PlainInvariantFinding(
                    finding_type="PLAIN",
                    code="BROKEN_INVARIANT",
                    severity=InvariantSeverity.ERROR,
                    message="unsafe",
                )
            ],
        )
    )
    assert isinstance(degraded, DegradedRuntimeSafetyProof)

    cut = ConsistentRuntimeSafetyCut(
        durable_revision=0,
        journal=JournalReadView.from_entries((), "test"),
        active_lease=None,
        effect_attempts=(),
        active_schedules=(),
    )

    class Auditor:
        def audit(self, observed=None) -> InvariantAudit:
            assert observed is cut
            return InvariantAudit(durable_revision=0, journal_revision=0)

    admitted = RuntimeSafetyService(Auditor(), gate).admit_cut(cut)  # type: ignore[arg-type]
    assert admitted is cut
    assert isinstance(gate.require_effects_allowed(), ProvenRuntimeSafetyProof)


def test_direct_effect_is_blocked_before_attempt_or_adapter(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    adapter = _NeverAdapter()
    operations, attempts, _, _ = direct_operation_stack(
        database, journal, adapter, runtime_safety=_degraded_service(database)
    )

    with pytest.raises(RuntimeSafetyBlocked):
        operations.run(tmp_path / "repo", OperationAction.UPDATE)

    assert adapter.calls == 0
    assert attempts.all() == []


def test_fleet_run_is_blocked_before_lease_or_journal_admission(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    live_output = GameTapeLiveOutputStore()
    run_execution = FleetRunExecution(
        lifecycle,
        FleetTargetScheduler(_UnusedExecutor()),  # type: ignore[arg-type]
        live_output,
    )
    manager = FleetRunControlPlane(
        _BlockedAdmission(_degraded_service(database)),  # type: ignore[arg-type]
        journal,
        lifecycle,
        run_execution,
        RunExecutionSupervisor(),
        live_output,
    )

    with pytest.raises(RuntimeSafetyBlocked):
        manager.start(OperationAction.UPDATE, fleet_run_id="fleet-blocked")

    assert journal.read_view().run_detail("fleet-blocked") is None
    assert execution.active_lease() is None
    manager.shutdown(wait=True)


def test_schedule_start_is_blocked_before_engine_admission() -> None:
    class Engine:
        calls = 0

        def start(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("Schedule engine must not run while degraded")

    engine = Engine()
    degraded = _degraded_service()
    service = ScheduleService(
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(find=lambda _schedule_run_id: None),  # type: ignore[arg-type]
        engine,  # type: ignore[arg-type]
        _BlockedAdmission(degraded),  # type: ignore[arg-type]
        degraded,
    )
    with pytest.raises(RuntimeSafetyBlocked):
        service.start(
            "schedule-1",
            "schedule-run-blocked",
            expected_generation_id="schedule-generation",
            expected_record_version=0,
        )
    assert engine.calls == 0



def test_unsafe_schedule_tick_settles_existing_state_without_scheduling_new_work() -> None:
    class UntouchedDefinitions:
        def __getattr__(self, name: str):
            raise AssertionError(f"Schedule definitions must not be touched while unsafe: {name}")

    class Engine:
        calls: list[bool] = []

        def advance_all(self, *, allow_effect_admission: bool) -> None:
            self.calls.append(allow_effect_admission)

    engine = Engine()

    service = ScheduleService(
        SimpleNamespace(),  # type: ignore[arg-type]
        UntouchedDefinitions(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        engine,  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(effects_allowed=lambda: False),  # type: ignore[arg-type]
    )

    service.tick()

    assert engine.calls == [False]


def test_runtime_safety_proof_variants_reject_contradictory_evidence() -> None:
    with pytest.raises(ValidationError):
        ProvenRuntimeSafetyProof(
            status=RuntimeSafetyStatus.PROVEN_SAFE,
            durable_revision=0,
        )

    warning = PlainInvariantFinding(
        finding_type="PLAIN",
        code="WARNING_ONLY",
        severity=InvariantSeverity.WARNING,
        message="warning",
    )
    with pytest.raises(ValidationError):
        DegradedRuntimeSafetyProof(
            status=RuntimeSafetyStatus.DEGRADED,
            durable_revision=0,
            findings=(warning,),
        )


def test_runtime_safety_service_refreshes_shared_gate() -> None:
    audits = [
        InvariantAudit(
            durable_revision=0,
            findings=[
                PlainInvariantFinding(
                    finding_type="PLAIN",
                    code="STALE_LEASE",
                    severity=InvariantSeverity.ERROR,
                    message="unsafe",
                )
            ],
        ),
        InvariantAudit(durable_revision=0, journal_revision=0),
    ]

    class Auditor:
        def current_durable_revision(self) -> int:
            return audits[0].durable_revision if audits else 0

        def audit(self) -> InvariantAudit:
            return audits.pop(0)

    gate = RuntimeSafetyGate()
    service = RuntimeSafetyService(Auditor(), gate)  # type: ignore[arg-type]
    assert service.refresh().status is RuntimeSafetyStatus.DEGRADED
    with pytest.raises(RuntimeSafetyBlocked):
        gate.require_effects_allowed()
    assert service.refresh().status is RuntimeSafetyStatus.PROVEN_SAFE
    gate.require_effects_allowed()


def test_scheduler_preserves_typed_domain_failure() -> None:
    class BackgroundConflict(StateConflict):
        code = "BACKGROUND_CONFLICT"
        retryable = True

    def tick() -> None:
        raise BackgroundConflict("scheduler conflict")

    scheduler = ScheduleScheduler(tick, interval_seconds=60)
    scheduler.tick()
    snapshot = scheduler.snapshot()

    assert snapshot.failures == 1
    assert snapshot.last_failure is not None
    assert snapshot.last_failure.code == "BACKGROUND_CONFLICT"
    assert snapshot.last_failure.kind is ErrorKind.CONFLICT
    assert snapshot.last_failure.retryable


def test_system_health_reports_degraded_runtime_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    proof = DegradedRuntimeSafetyProof(
        status=RuntimeSafetyStatus.DEGRADED,
        durable_revision=0,
        findings=[
            PlainInvariantFinding(
                finding_type="PLAIN",
                code="JOURNAL_INTEGRITY_FAILED",
                severity=InvariantSeverity.ERROR,
                message="corrupt",
            )
        ],
    )
    service = SystemHealthService(SimpleNamespace(refresh=lambda: proof), lambda: [])  # type: ignore[arg-type]
    # Avoid coupling this assertion to host-installed tool versions; the runtime proof alone drives DEGRADED.
    from oh_goon.core.vocabulary import ToolStatus

    monkeypatch.setattr(
        service,
        "_inspect_tool",
        lambda name, required: ReadyToolHealth(
            name=name, required=required, status=ToolStatus.READY
        ),
    )
    health = service.inspect()
    assert health.status is SystemHealthStatus.DEGRADED
    assert not health.runtime_safety.effects_allowed


def test_scheduler_stop_owns_lifecycle_lock_across_join() -> None:
    import threading

    join_entered = threading.Event()
    release_join = threading.Event()

    class BlockingThread:
        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            join_entered.set()
            assert release_join.wait(timeout=1.0)
            self.alive = False

    scheduler = ScheduleScheduler(lambda: None, interval_seconds=0.01)
    scheduler._thread = BlockingThread()  # type: ignore[assignment]

    stopping = threading.Thread(target=scheduler.stop)
    stopping.start()
    assert join_entered.wait(timeout=1.0)

    assert not scheduler._lifecycle_lock.acquire(blocking=False)

    release_join.set()
    stopping.join(timeout=1.0)
    assert not stopping.is_alive()
    assert scheduler.snapshot().running is False
