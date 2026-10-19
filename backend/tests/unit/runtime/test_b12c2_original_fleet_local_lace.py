"""B12C2: original Fleet lease and causal journal are one pre-physical cut."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceReservation
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import (
    _backend,
    _execute,
    _request,
)
from tests.unit.runtime.test_b12_local_resource_intent import _store


class _OriginalSession:
    """Native transaction semantics mimic ExecutionSession.mutate, not a new signer."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.token = "lease-one"
        self.cancelled = False
        self.finished = False
        self.commit_count = 0

    def original_fleet_lease_token(self) -> str:
        if self.finished:
            raise CausalJournalError("original session finished")
        return self.token

    def cancel_requested_in(self, connection: sqlite3.Connection) -> bool:
        assert connection is self.connection
        return self.cancelled

    def mutate(self, action):
        if self.finished:
            raise CausalJournalError("original session finished")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            value = action(self.connection)
            self.connection.execute("COMMIT")
            self.commit_count += 1
            return value
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise


def _original_reservation(tmp_path: Path, monkeypatch):
    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    connection, journal = _store(tmp_path / "original.sqlite")
    session = _OriginalSession(connection)
    checks: list[str] = []

    def revalidate(connection: sqlite3.Connection) -> None:
        assert connection is session.connection
        checks.append("original-execution-authority")

    reservation = OriginalFleetLocalLaceReservation(
        session=session,
        validate_authority_in=revalidate,
        authenticator=authenticator(),
        run_id="run-one",
        step_id="step-1",
        attempt_number=1,
        command_ordinal=1,
        attempt_root=request.attempt.paths.root,
        execution_environment_identity="sha256:" + "a" * 64,
        backend_capability_manifest_identity="sha256:" + "b" * 64,
        backend_configuration_identity=backend.configuration_identity(),
    )
    request.local_resource_intent = reservation
    return backend, touched, request, connection, journal, session, reservation, checks


def test_original_fleet_owner_commits_and_revalidates_before_native_allocation(
    tmp_path, monkeypatch
):
    backend, touched, request, conn, journal, session, _, checks = _original_reservation(
        tmp_path, monkeypatch
    )
    try:
        with pytest.raises(RuntimeError, match="PHYSICAL_RESOURCE_CREATION_REACHED"):
            _execute(backend, request)
        assert touched.call_count == 1
        assert checks and set(checks) == {"original-execution-authority"}
        assert [e["kind"] for e in journal.verify("run-one", auth=authenticator())] == [
            "RUN_ADMITTED",
            "RESOURCE_INTENT",
        ]
        with pytest.raises(CausalJournalError, match="duplicate"):
            _execute(backend, request)
        assert touched.call_count == 1
    finally:
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "wrong-session-lease",
        "cancelled",
        "finished",
        "wrong-authenticator",
        "control-epoch-takeover",
        "original-authority-revoked",
        "postappend-revocation",
    ],
)
def test_original_fleet_denial_never_reaches_native_allocation(tmp_path, monkeypatch, attack):
    from dataclasses import replace

    backend, touched, request, conn, journal, session, permit, checks = _original_reservation(
        tmp_path, monkeypatch
    )
    try:
        if attack == "wrong-session-lease":
            session.token = "other-owner"
        elif attack == "cancelled":
            session.cancelled = True
        elif attack == "finished":
            session.finished = True
        elif attack == "wrong-authenticator":
            request.local_resource_intent = replace(permit, authenticator=authenticator("foreign"))
        elif attack == "control-epoch-takeover":
            journal.transfer_control(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                next_epoch=2,
                next_lease_identity="new-owner",
            )
        elif attack in {"original-authority-revoked", "postappend-revocation"}:

            def reject_original(_connection):
                if attack == "original-authority-revoked" or checks:
                    raise TrustProtocolError("original Fleet authority revoked")
                checks.append("first-read")

            request.local_resource_intent = replace(permit, validate_authority_in=reject_original)
        with pytest.raises((CausalJournalError, TrustProtocolError)):
            _execute(backend, request)
        touched.assert_not_called()
        assert session.commit_count == 0
        assert [e["kind"] for e in journal.verify("run-one", auth=authenticator())] == [
            "RUN_ADMITTED"
        ]
    finally:
        conn.close()


def test_root_configuration_requires_prework_intent_in_admitted_identity(tmp_path):
    from oh_goon.runtime._executor_configuration import _bubblewrap_backend

    native = tmp_path / "bwrap"
    native.write_bytes(b"a test executable")
    backend = _bubblewrap_backend(
        {"bwrap_executable": str(native)},
        source=tmp_path / "executors.json",
        cgroup_root=tmp_path / "cgroup",
    )
    assert isinstance(backend, BubblewrapProcessIsolation)
    assert backend.require_local_resource_intent is True
    old = BubblewrapProcessIsolation(str(native), cgroup_root=tmp_path / "cgroup")
    assert backend.configuration_identity() != old.configuration_identity()


def test_fleet_target_scheduler_transports_original_factory_across_workers():
    from contextlib import nullcontext
    from types import SimpleNamespace

    from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler

    observed = []
    trusted = Mock()

    class Effects:
        def execute_fleet_target(self, *args, **kwargs):
            observed.append(kwargs.get("local_lace_intent_for"))

    session = SimpleNamespace(execution_connection_scope=nullcontext)
    targets = [
        SimpleNamespace(repository=SimpleNamespace(path=Path(f"/repo{i}"), repository_id=f"r{i}"))
        for i in range(3)
    ]
    FleetTargetScheduler(Effects()).execute(
        object(),
        object(),
        targets,
        session,
        parallel_safe=True,
        max_parallel=2,
        repository_admissions=(),
        local_lace_intent_for=trusted,
    )
    assert len(observed) == 3
    assert all(item is trusted for item in observed)


def test_strict_run_transports_original_factory_into_step_runtime(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import oh_goon.integrations.fleet_plan_run_execution as owner
    from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose

    trusted = Mock()
    captured = []

    class Sequence:
        results = ()
        stdout = ""
        stderr = ""

        def __init__(self, repository, runtime, output_observer):
            captured.append(runtime.local_resource_intent_for)

        def run_strict(self, prepared, step):
            captured.append(step.step_id)

    monkeypatch.setattr(owner, "FleetPlanStepSequence", Sequence)
    executor = owner.FleetPlanRunExecutor(
        git=Mock(),
        process_isolation_backends=(),
    )
    prepared = SimpleNamespace(
        plan_id="plan-one",
        trusted_local_scope=None,
        trusted_local_consent=None,
        bind_execution_cancel_requested=Mock(),
        execution_input_evidence=lambda: None,
    )
    plan = SimpleNamespace(
        plan_id="plan-one",
        execution=SimpleNamespace(purpose=ExecutionPurpose.CICD),
        steps=(SimpleNamespace(step_id="step-1"),),
    )
    result = executor.execute_strict_run(
        tmp_path,
        plan,
        prepared,
        cancel_requested=None,
        fleet_run_id="run-one",
        local_lace_intent_for=trusted,
    )
    assert captured == [trusted, "step-1"]
    assert result.steps == ()


def test_original_run_execution_transports_trusted_factory_to_targets(monkeypatch):
    from types import SimpleNamespace

    import oh_goon.services.fleet_run_execution as owner
    from oh_goon.core.vocabulary import FleetRunTerminalIntent
    from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore

    trusted = Mock()
    session = SimpleNamespace(
        require_fleet_run_id=lambda: "run-one",
        cancel_requested=lambda: False,
    )
    authority = SimpleNamespace(
        capability=object(),
        target=None,
        targets=(),
        execution=SimpleNamespace(kind="direct"),
        repository_admissions=(),
    )

    class Lifecycle:
        execution_authority = staticmethod(lambda _session: authority)
        begin = staticmethod(lambda _session: None)
        local_lace_intent_for = staticmethod(lambda _session, _authority: trusted)
        require_execution_authority_in = staticmethod(lambda *args: None)

    monkeypatch.setattr(
        owner,
        "resolve_bound_capability_intent",
        lambda *args: SimpleNamespace(parallel_safe=False, max_parallel=1),
    )
    monkeypatch.setattr(owner, "require_target_caller_file_fresh", lambda target: None)
    captured = {}

    class Targets:
        def execute(self, *args, **kwargs):
            captured.update(kwargs)

    runner = owner.FleetRunExecution(Lifecycle(), Targets(), GameTapeLiveOutputStore())
    terminal, failure = runner._run(session)
    assert terminal is FleetRunTerminalIntent.COMPLETE and failure is None
    assert captured["local_lace_intent_for"] is trusted
