from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oh_goon.runtime.local_launch_context import (
    LOCAL_LAUNCH_ID_ENV,
    LocalLaunchRuntimeContext,
)
from oh_goon.runtime.local_launch_owner import LocalLaunchOwner
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.local_launch_plan import LocalLaunchPlan, LocalServicePlan
from oh_goon.runtime.local_launch_supervisor import stop_process_group


def test_stop_process_group_kills_descendant_after_group_leader_exits(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    leader = subprocess.Popen(
        [sys.executable, "-c", (
            "import subprocess,sys; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"open({str(pid_file)!r},'w').write(str(p.pid))"
        )],
        process_group=0,
    )
    pgid = leader.pid
    leader.wait(timeout=5)
    deadline = time.monotonic() + 2
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    child_pid = int(pid_file.read_text())
    assert leader.poll() is not None
    os.kill(child_pid, 0)
    stop_process_group(pgid, (leader,))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        # A dead-but-unreaped grandchild can remain a zombie briefly in containers;
        # /proc state Z still proves it is no longer executing.
        state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
        assert state == "Z"


def test_startup_budget_is_one_plan_level_value() -> None:
    plan = LocalLaunchPlan(profile="test", services=(
        LocalServicePlan("one", 1, ("true",), None, "127.0.0.1", 1, "/", 200),
        LocalServicePlan("two", 1, ("true",), None, "127.0.0.1", 2, "/", 200),
    ))
    assert plan.startup_timeout_seconds == 30.0
    assert not hasattr(plan.services[0], "timeout_seconds")
    assert not hasattr(plan.services[1], "timeout_seconds")

class _FakeProcess:
    def __init__(self, codes):
        self._codes = iter(codes)
        self._last = None
    def poll(self):
        try:
            self._last = next(self._codes)
        except StopIteration:
            pass
        return self._last


class _FakeOwner:
    def __init__(self):
        self.events = []
    def service_failed(self, *args, **kwargs): self.events.append(("service_failed", args, kwargs))
    def launch_failed(self, *args, **kwargs): self.events.append(("launch_failed", args, kwargs))
    def bound(self, *args, **kwargs): self.events.append(("bound", args, kwargs))
    def startup_complete(self, *args, **kwargs): self.events.append(("startup_complete", args, kwargs))
    def ready(self, *args, **kwargs): self.events.append(("ready", args, kwargs))
    def launch_ready(self): self.events.append(("launch_ready", (), {}))


def _two_service_plan(timeout=30.0):
    return LocalLaunchPlan(profile="test", startup_timeout_seconds=timeout, services=(
        LocalServicePlan("backend", 1, ("backend",), None, "127.0.0.1", 1, "/healthz", 204),
        LocalServicePlan("frontend", 1, ("frontend",), None, "127.0.0.1", 2, "/", 200),
    ))


def test_required_service_exit_wins_over_readiness_timeout() -> None:
    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError, wait_for_services_ready
    owner = _FakeOwner()
    try:
        wait_for_services_ready(
            owner=owner, plan=_two_service_plan(),
            processes=(_FakeProcess([7]), _FakeProcess([None])),
            now=lambda: 0.0, sleep=lambda _: None, probe=lambda _: (False, "refused"),
        )
    except LocalLaunchStartupError as exc:
        assert "backend exited before readiness (status 7)" in str(exc)
    else:
        raise AssertionError("service death must fail startup")
    assert owner.events[0][0] == "service_failed"
    assert owner.events[1][0] == "launch_failed"


def test_required_service_exit_includes_bounded_redacted_startup_output() -> None:
    import collections

    import pytest

    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError, wait_for_services_ready

    owner = _FakeOwner()
    logs = collections.deque(
        [
            "[backend] booting",
            "[backend] Authorization: Bearer super-secret-token",
            "[frontend] unrelated",
            "[backend] fatal startup detail",
        ],
        maxlen=80,
    )
    with pytest.raises(LocalLaunchStartupError) as exc:
        wait_for_services_ready(
            owner=owner, plan=_two_service_plan(),
            processes=(_FakeProcess([7]), _FakeProcess([None])),
            now=lambda: 0.0, sleep=lambda _: None, probe=lambda _: (False, "refused"),
            log_tail=logs,
        )

    message = str(exc.value)
    assert "startup output (redacted)" in message
    assert "fatal startup detail" in message
    assert "super-secret-token" not in message
    assert "[frontend] unrelated" not in message


def test_all_services_share_one_absolute_startup_budget() -> None:
    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError, wait_for_services_ready
    owner = _FakeOwner()
    clock = [0.0]
    def now(): return clock[0]
    def sleep(seconds): clock[0] += seconds
    def probe(service):
        if service.service == "backend": return True, "HTTP 204"
        return False, "connection refused"
    try:
        wait_for_services_ready(
            owner=owner, plan=_two_service_plan(timeout=0.3),
            processes=(_FakeProcess([None]), _FakeProcess([None])),
            now=now, sleep=sleep, probe=probe,
        )
    except LocalLaunchStartupError as exc:
        assert "within 0.3 seconds" in str(exc)
    else:
        raise AssertionError("startup budget must expire")
    assert 0.3 <= clock[0] < 0.41
    assert sum(1 for event in owner.events if event[0] == "ready") == 1
    assert owner.events[-1][0] == "launch_failed"


def test_ready_publishes_only_after_every_required_service_is_ready() -> None:
    from oh_goon.runtime.local_launch_supervisor import wait_for_services_ready
    owner = _FakeOwner()
    wait_for_services_ready(
        owner=owner, plan=_two_service_plan(),
        processes=(_FakeProcess([None]), _FakeProcess([None])),
        now=lambda: 0.0, sleep=lambda _: None, probe=lambda _: (True, "ready"),
    )
    assert owner.events[-1][0] == "launch_ready"
    assert [event[0] for event in owner.events].count("ready") == 2


def test_inprocess_readiness_uses_one_budget_and_records_structured_failure(tmp_path: Path) -> None:
    from oh_goon.runtime.local_launch_plan import built_local_plan
    from oh_goon.runtime.local_launch_supervisor import start_inprocess_readiness_monitor

    store = LocalLaunchEventStore.from_database(tmp_path / "state" / "oh-goon.db")
    plan = built_local_plan()
    owner = LocalLaunchOwner.create(
        intent=plan.intent(), workspace=tmp_path, asserted_by="test", store=store
    )
    owner.spawned("backend", 1, os.getpid())
    clock = iter((0.0, 0.0, 30.0, 30.0))
    monitor = start_inprocess_readiness_monitor(
        owner=owner,
        plan=plan,
        now=lambda: next(clock),
        sleep=lambda _seconds: None,
        probe=lambda _service: (False, "connection refused"),
    )
    monitor.thread.join(timeout=1.0)
    assert not monitor.thread.is_alive()
    manifest = store.manifest(owner.identity.launch_id)
    assert manifest.lifecycle.state.value == "failed"
    assert manifest.failure is not None
    assert manifest.failure.code == "readiness_timeout"
    assert "30 second startup budget exhausted" in manifest.failure.reason


def test_inprocess_readiness_failure_requests_native_server_shutdown(tmp_path: Path) -> None:
    from oh_goon.runtime.local_launch_plan import built_local_plan
    from oh_goon.runtime.local_launch_supervisor import start_inprocess_readiness_monitor

    store = LocalLaunchEventStore.from_database(tmp_path / "state" / "oh-goon.db")
    plan = built_local_plan()
    owner = LocalLaunchOwner.create(
        intent=plan.intent(), workspace=tmp_path, asserted_by="test", store=store
    )
    owner.spawned("backend", 1, os.getpid())
    requested: list[str] = []
    clock = iter((0.0, 0.0, 30.0, 30.0))
    monitor = start_inprocess_readiness_monitor(
        owner=owner,
        plan=plan,
        on_failure=lambda: requested.append("shutdown"),
        now=lambda: next(clock),
        sleep=lambda _seconds: None,
        probe=lambda _service: (False, "connection refused"),
    )
    monitor.thread.join(timeout=1.0)
    assert requested == ["shutdown"]



def test_immediate_exit_is_valid_after_spawn_registration(tmp_path: Path) -> None:
    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError, wait_for_services_ready

    store = LocalLaunchEventStore.from_database(tmp_path / "state" / "oh-goon.db")
    plan = LocalLaunchPlan(profile="test", services=(
        LocalServicePlan("backend", 1, ("backend",), None, "127.0.0.1", 1, "/", 204),
    ))
    owner = LocalLaunchOwner.create(intent=plan.intent(), workspace=tmp_path, asserted_by="test", store=store)
    owner.spawned("backend", 1, os.getpid())
    try:
        wait_for_services_ready(
            owner=owner, plan=plan, processes=(_FakeProcess([7]),),
            now=lambda: 0.0, sleep=lambda _: None, probe=lambda _: (False, "refused"),
        )
    except LocalLaunchStartupError:
        pass
    else:
        raise AssertionError("immediate exit must fail startup")
    manifest = store.manifest(owner.identity.launch_id)
    assert manifest.lifecycle.state.value == "failed"
    kinds = [event.event.value for event in store.events(owner.identity.launch_id)]
    assert kinds.index("service.spawned") < kinds.index("service.failed")


def test_spawn_bridge_ack_is_required_before_runtime_observation(monkeypatch) -> None:
    import oh_goon.runtime.local_launch_supervisor as supervisor

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"1")
    os.close(write_fd)
    process = _FakeProcess([None])
    supervisor._await_spawn_registration(
        owner=_FakeOwner(), service=_two_service_plan().services[0], process=process,
        read_fd=read_fd, deadline=1.0, now=lambda: 0.0,
    )


def test_spawn_bridge_close_fails_before_readiness() -> None:
    import oh_goon.runtime.local_launch_supervisor as supervisor

    owner = _FakeOwner()
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    try:
        supervisor._await_spawn_registration(
            owner=owner, service=_two_service_plan().services[0], process=_FakeProcess([7]),
            read_fd=read_fd, deadline=1.0, now=lambda: 0.0,
        )
    except supervisor.LocalLaunchStartupError as exc:
        assert "before service.spawned registration" in str(exc)
    else:
        raise AssertionError("closed spawn bridge must fail startup")
    assert owner.events[0][0] == "launch_failed"


def test_spawn_helper_kills_child_when_registration_bridge_fails(tmp_path: Path, monkeypatch) -> None:
    import oh_goon.runtime.local_launch_supervisor as supervisor

    fake_exec = tmp_path / "backend/.venv/bin/oh-goon-local-launch-exec"
    fake_exec.parent.mkdir(parents=True)
    fake_exec.write_text("#!/bin/sh\nexit 7\n")
    fake_exec.chmod(0o755)
    killed: list[tuple[int, tuple[object, ...]]] = []
    monkeypatch.setattr(supervisor, "stop_process_group", lambda pgid, processes: killed.append((pgid, processes)))
    service = LocalServicePlan("backend", 1, ("unused",), tmp_path, "127.0.0.1", 1, "/", 204)
    owner = type("Owner", (), {
        "identity": type("Identity", (), {"launch_id": "launch-test"})(),
        "launch_failed": lambda self, **_: None,
    })()
    try:
        supervisor._spawn_registered_service(
            root=tmp_path, owner=owner, service=service, child_env=dict(os.environ),
            pgid=None, deadline=time.monotonic() + 2, now=time.monotonic,
        )
    except supervisor.LocalLaunchStartupError:
        pass
    else:
        raise AssertionError("spawn registration failure must fail startup")
    assert len(killed) == 1
    assert killed[0][0] == killed[0][1][0].pid



def test_contributor_launch_state_uses_supplied_environment_database(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.runtime.local_launch_supervisor as supervisor
    from oh_goon.core.path_layouts import ApplicationStatePathLayout

    ambient_db = tmp_path / "ambient" / "oh-goon.db"
    supplied_db = tmp_path / "supplied" / "oh-goon.db"
    monkeypatch.setenv("OH_GOON_DB", str(ambient_db))

    class _StartedProcess:
        pid = 43210

        def poll(self):
            return None

    monkeypatch.setattr(
        supervisor,
        "_spawn_registered_service",
        lambda **_kwargs: _StartedProcess(),
    )
    monkeypatch.setattr(supervisor, "wait_for_services_ready", lambda **_kwargs: None)

    plan = LocalLaunchPlan(
        profile="test",
        services=(
            LocalServicePlan(
                "backend", 1, ("unused",), tmp_path, "127.0.0.1", 8787, "/healthz", 204
            ),
        ),
    )
    running = supervisor.start_contributor_launch(
        root=tmp_path,
        env={**os.environ, "OH_GOON_DB": str(supplied_db)},
        plan=plan,
        token="test-token",
    )

    supplied = ApplicationStatePathLayout.from_database(supplied_db)
    ambient = ApplicationStatePathLayout.from_database(ambient_db)
    assert list(supplied.local_launches.glob("*.json"))
    assert list(supplied.local_launch_bindings.glob("*.json"))
    assert list(supplied.local_launch_access.glob("*.json"))
    assert not ambient.local_launches.exists()
    assert not ambient.local_launch_bindings.exists()
    assert not ambient.local_launch_access.exists()
    running.owner.finish_owned_launch()


def test_contributor_launch_transports_one_canonical_runtime_context(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.runtime.local_launch_supervisor as supervisor

    database = tmp_path / "state" / "oh-goon.db"
    captured_env: dict[str, str] = {}

    class _StartedProcess:
        pid = 43210

        def poll(self):
            return None

    def spawn(**kwargs):
        captured_env.update(kwargs["child_env"])
        return _StartedProcess()

    monkeypatch.setattr(supervisor, "BACKEND_GENERATION", 7)
    monkeypatch.setattr(supervisor, "_spawn_registered_service", spawn)
    monkeypatch.setattr(supervisor, "wait_for_services_ready", lambda **_kwargs: None)
    plan = LocalLaunchPlan(
        profile="test",
        services=(
            LocalServicePlan(
                "backend", 1, ("unused",), tmp_path, "127.0.0.1", 8787, "/healthz", 204
            ),
        ),
    )

    running = supervisor.start_contributor_launch(
        root=tmp_path,
        env={**os.environ, "OH_GOON_DB": str(database)},
        plan=plan,
        token="test-token",
    )

    context = LocalLaunchRuntimeContext.from_environment(captured_env)
    assert context is not None
    assert context.launch_id == running.owner.identity.launch_id
    assert context.owner_generation == running.owner.identity.owner_generation
    assert context.backend_generation == 7
    running.owner.finish_owned_launch()


def test_contributor_launch_rejects_incomplete_runtime_context_before_spawn(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.runtime.local_launch_supervisor as supervisor

    database = tmp_path / "state" / "oh-goon.db"
    admitted: list[object] = []

    def install_incomplete(self, *, backend_generation, environ):
        environ[LOCAL_LAUNCH_ID_ENV] = self.identity.launch_id
        return LocalLaunchRuntimeContext(
            launch_id=self.identity.launch_id,
            owner_generation=self.identity.owner_generation,
            backend_generation=backend_generation,
        )

    def fail_spawn(**kwargs):
        admitted.append(kwargs)
        raise AssertionError("spawned")

    monkeypatch.setattr(supervisor.LocalLaunchOwner, "install_runtime_context", install_incomplete)
    monkeypatch.setattr(supervisor, "_spawn_registered_service", fail_spawn)
    plan = LocalLaunchPlan(
        profile="test",
        services=(
            LocalServicePlan(
                "backend", 1, ("unused",), tmp_path, "127.0.0.1", 8787, "/healthz", 204
            ),
        ),
    )

    with pytest.raises(supervisor.LocalLaunchStartupError, match="runtime context transport"):
        supervisor.start_contributor_launch(
            root=tmp_path,
            env={**os.environ, "OH_GOON_DB": str(database)},
            plan=plan,
            token="test-token",
        )
    assert admitted == []


def test_readiness_timeout_exposes_bounded_structured_failure_evidence() -> None:
    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError, wait_for_services_ready

    owner = _FakeOwner()
    plan = _two_service_plan(timeout=0.2)
    clock = [0.0]
    def now(): return clock[0]
    def sleep(seconds): clock[0] += seconds
    try:
        wait_for_services_ready(
            owner=owner, plan=plan,
            processes=(_FakeProcess([None]), _FakeProcess([None])),
            now=now, sleep=sleep,
            probe=lambda service: (False, f"{service.service}-not-ready"),
        )
    except LocalLaunchStartupError as exc:
        evidence = exc.evidence
    else:
        raise AssertionError("expected startup failure")
    assert evidence.code == "readiness_timeout"
    assert evidence.startup_budget_seconds == plan.startup_timeout_seconds
    assert evidence.elapsed_seconds == plan.startup_timeout_seconds
    assert dict(evidence.readiness_observations) == {
        "backend": "backend-not-ready",
        "frontend": "frontend-not-ready",
    }
    assert len(repr(evidence)) < 1000


def test_required_child_exit_exposes_service_identity_without_token() -> None:
    from oh_goon.runtime.local_launch_supervisor import LocalLaunchStartupError, wait_for_services_ready

    owner = _FakeOwner()
    try:
        wait_for_services_ready(
            owner=owner, plan=_two_service_plan(),
            processes=(_FakeProcess([17]), _FakeProcess([None])),
            now=lambda: 0.0, sleep=lambda _seconds: None,
            probe=lambda _service: (False, "not-ready"),
        )
    except LocalLaunchStartupError as exc:
        evidence = exc.evidence
    else:
        raise AssertionError("expected startup failure")
    assert evidence.code == "required_service_exit"
    assert evidence.service == "backend"
    assert evidence.generation == 1
    assert "token" not in repr(evidence).lower()
