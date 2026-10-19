from __future__ import annotations

import collections
import os
import select
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from oh_goon.core import local_access_protocol
from oh_goon.core.diagnostic_redaction import redact_diagnostic_text
from oh_goon.runtime.database import database_path_for_environment
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_binding import LocalLaunchBindingStore
from oh_goon.runtime.local_launch_context import LocalLaunchRuntimeContext
from oh_goon.runtime.local_launch_owner import BACKEND_GENERATION, LocalLaunchOwner
from oh_goon.runtime.local_launch_plan import LocalLaunchPlan, LocalServicePlan
from oh_goon.runtime.local_launch_reuse import LocalLaunchReuseAction, assess_local_launch_reuse
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


@dataclass(frozen=True, slots=True)
class RunningLocalLaunch:
    owner: LocalLaunchOwner
    token: str
    processes: tuple[subprocess.Popen[str], ...]
    process_group: int
    log_tail: collections.deque[str]

    def stop(self) -> None:
        stop_process_group(self.process_group, self.processes)
        self.owner.finish_owned_launch()

    def crash(self) -> None:
        crash_process_group(self.process_group, self.processes)

    def diagnostic_tail(self, *, limit: int = 20) -> tuple[str, ...]:
        """Return bounded, redacted launch output for acceptance failure diagnosis."""
        if limit < 1:
            raise ValueError("diagnostic tail limit must be positive")
        return tuple(redact_diagnostic_text(line.rstrip()) for line in list(self.log_tail)[-limit:])


@dataclass(frozen=True, slots=True)
class LocalLaunchFailureEvidence:
    code: str
    reason: str
    launch_id: str | None = None
    service: str | None = None
    generation: int | None = None
    elapsed_seconds: float | None = None
    startup_budget_seconds: float | None = None
    readiness_observations: tuple[tuple[str, str], ...] = ()


class LocalLaunchStartupError(RuntimeError):
    def __init__(self, reason: str, *, evidence: LocalLaunchFailureEvidence | None = None) -> None:
        super().__init__(reason)
        self.evidence = evidence or LocalLaunchFailureEvidence(code="startup_failed", reason=reason)


def probe_http(service: LocalServicePlan) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(service.readiness_url, timeout=0.5) as response:
            status = response.status
    except (OSError, urllib.error.URLError) as exc:
        return False, str(exc)
    return status == service.readiness_status, f"HTTP {status}"


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def crash_process_group(pgid: int, processes: tuple[subprocess.Popen[str], ...]) -> None:
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def stop_process_group(pgid: int, processes: tuple[subprocess.Popen[str], ...]) -> None:
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10.0
    while _group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for process in processes:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _launch_id(owner: object) -> str | None:
    identity = getattr(owner, "identity", None)
    value = getattr(identity, "launch_id", None)
    return value if isinstance(value, str) else None


def _fail_spawn_registration(
    owner: LocalLaunchOwner, service: LocalServicePlan, *, code: str, reason: str
) -> None:
    owner.launch_failed(code=code, reason=reason, retryable=True, failure_kind="process")
    raise LocalLaunchStartupError(
        reason,
        evidence=LocalLaunchFailureEvidence(
            code=code,
            reason=reason,
            launch_id=_launch_id(owner),
            service=service.service,
            generation=service.generation,
        ),
    )


def _accept_spawn_marker(
    owner: LocalLaunchOwner, service: LocalServicePlan, process: subprocess.Popen[str], read_fd: int
) -> bool:
    marker = os.read(read_fd, 1)
    if marker == b"1":
        return True
    status = process.poll()
    suffix = f" (status {status})" if status is not None else ""
    _fail_spawn_registration(
        owner,
        service,
        code="spawn_registration_failed",
        reason=f"{service.service} spawn bridge closed before service.spawned registration{suffix}",
    )
    return False


def _await_spawn_registration(
    *,
    owner: LocalLaunchOwner,
    service: LocalServicePlan,
    process: subprocess.Popen[str],
    read_fd: int,
    deadline: float,
    now: Callable[[], float],
) -> None:
    """Do not expose a child to runtime observation before durable spawn evidence."""
    try:
        while deadline > now():
            readable, _, _ = select.select([read_fd], [], [], min(0.1, deadline - now()))
            if readable and _accept_spawn_marker(owner, service, process, read_fd):
                return
            status = process.poll()
            if status is not None:
                _fail_spawn_registration(
                    owner,
                    service,
                    code="spawn_registration_failed",
                    reason=f"{service.service} spawn bridge exited before service.spawned registration (status {status})",
                )
        _fail_spawn_registration(
            owner,
            service,
            code="spawn_registration_timeout",
            reason=f"{service.service} did not register service.spawned within the startup budget",
        )
    finally:
        os.close(read_fd)


def _capture_service_output(
    process: subprocess.Popen[str],
    *,
    service: LocalServicePlan,
    log_tail: collections.deque[str],
) -> None:
    stream = process.stdout
    if stream is None:
        return
    for line in stream:
        rendered = redact_diagnostic_text(line.rstrip())
        if rendered:
            log_tail.append(f"[{service.service}] {rendered}")


def _startup_log_tail(
    log_tail: collections.deque[str] | None,
    *,
    service: str,
    limit: int = 12,
) -> str:
    if not log_tail:
        return ""
    prefix = f"[{service}] "
    selected = [redact_diagnostic_text(line) for line in log_tail if line.startswith(prefix)][
        -limit:
    ]
    if not selected:
        return ""
    return "\nstartup output (redacted):\n" + "\n".join(selected)


def _spawn_registered_service(
    *,
    root: Path,
    owner: LocalLaunchOwner,
    service: LocalServicePlan,
    child_env: dict[str, str],
    pgid: int | None,
    deadline: float,
    now: Callable[[], float],
    log_tail: collections.deque[str] | None = None,
) -> subprocess.Popen[str]:
    read_fd, write_fd = os.pipe()
    wrapper = [
        str(root / "backend/.venv/bin/oh-goon-local-launch-exec"),
        "--launch-id",
        owner.identity.launch_id,
        "--service",
        service.service,
        "--generation",
        str(service.generation),
        "--asserted-by",
        "contributor-dev-supervisor",
        "--spawn-ready-fd",
        str(write_fd),
    ]
    if service.cwd is not None:
        wrapper += ["--cwd", str(service.cwd)]
    wrapper += ["--", *service.command]
    try:
        process = subprocess.Popen(
            wrapper,
            cwd=root,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            process_group=0 if pgid is None else pgid,
            pass_fds=(write_fd,),
        )
        if log_tail is not None:
            threading.Thread(
                target=_capture_service_output,
                kwargs={"process": process, "service": service, "log_tail": log_tail},
                daemon=True,
                name=f"oh-goon-{service.service}-startup-output",
            ).start()
    except BaseException:
        os.close(read_fd)
        raise
    finally:
        os.close(write_fd)
    try:
        _await_spawn_registration(
            owner=owner,
            service=service,
            process=process,
            read_fd=read_fd,
            deadline=deadline,
            now=now,
        )
    except BaseException:
        # The caller cannot own this process until registration succeeds, so this
        # helper must close the physical process/group on every registration failure.
        stop_process_group(process.pid if pgid is None else pgid, (process,))
        raise
    return process


def _fail_if_required_service_exited(
    owner, plan, processes, log_tail: collections.deque[str] | None = None
) -> None:
    exited = None
    for service, process in zip(plan.services, processes, strict=True):
        code = process.poll()
        if code is not None:
            exited = (service, code)
            break
    if exited is None:
        return
    service, code = exited
    assert code is not None
    reason = f"{service.service} exited before readiness (status {code})" + _startup_log_tail(
        log_tail, service=service.service
    )
    owner.service_failed(
        service.service,
        service.generation,
        code="child_exit",
        reason=f"{service.service} exited with status {code}",
        retryable=True,
    )
    owner.launch_failed(
        code="required_service_exit",
        reason=f"required local service {service.service} exited with status {code}",
        retryable=True,
    )
    raise LocalLaunchStartupError(
        reason,
        evidence=LocalLaunchFailureEvidence(
            code="required_service_exit",
            reason=reason,
            launch_id=_launch_id(owner),
            service=service.service,
            generation=service.generation,
        ),
    )


def _observe_readiness(owner, plan, ready, last, probe) -> None:
    for service in plan.services:
        if service.service in ready:
            continue
        ok, detail = probe(service)
        last[service.service] = detail
        if not ok:
            continue
        owner.bound(service.service, service.generation, host=service.host, port=service.port)
        if service.service == "frontend":
            owner.startup_complete(service.service, service.generation)
        owner.ready(service.service, service.generation)
        ready.add(service.service)


def wait_for_services_ready(
    *,
    owner: LocalLaunchOwner,
    plan: LocalLaunchPlan,
    processes: tuple[subprocess.Popen[str], ...],
    now: Callable[[], float],
    sleep: Callable[[float], None],
    probe: Callable[[LocalServicePlan], tuple[bool, str]],
    deadline: float | None = None,
    log_tail: collections.deque[str] | None = None,
) -> None:
    deadline = now() + plan.startup_timeout_seconds if deadline is None else deadline
    ready: set[str] = set()
    last: dict[str, str] = {}
    while len(ready) != len(plan.services):
        _fail_if_required_service_exited(owner, plan, processes, log_tail)
        _observe_readiness(owner, plan, ready, last, probe)
        if len(ready) == len(plan.services):
            break
        if now() >= deadline:
            detail = ", ".join(
                f"{name}={last.get(name, 'not probed')}"
                for name in (s.service for s in plan.services)
                if name not in ready
            )
            reason = f"local launch did not become ready within {plan.startup_timeout_seconds:g} seconds; {detail}"
            owner.launch_failed(
                code="readiness_timeout",
                reason=f"{plan.startup_timeout_seconds:g} second startup budget exhausted; {detail}",
                retryable=True,
                failure_kind="readiness",
            )
            raise LocalLaunchStartupError(
                reason,
                evidence=LocalLaunchFailureEvidence(
                    code="readiness_timeout",
                    reason=reason,
                    launch_id=_launch_id(owner),
                    elapsed_seconds=plan.startup_timeout_seconds,
                    startup_budget_seconds=plan.startup_timeout_seconds,
                    readiness_observations=tuple(sorted(last.items())),
                ),
            )
        sleep(min(0.1, max(0.0, deadline - now())))
    owner.launch_ready()


def start_contributor_launch(
    *,
    root: Path,
    env: dict[str, str],
    plan: LocalLaunchPlan,
    token: str,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    probe: Callable[[LocalServicePlan], tuple[bool, str]] = probe_http,
) -> RunningLocalLaunch:
    workspace = root.resolve()
    database_path = database_path_for_environment(env)
    events = LocalLaunchEventStore.from_database(database_path)
    bindings = LocalLaunchBindingStore.from_event_store(events)
    intent = plan.intent()
    decision = assess_local_launch_reuse(
        workspace=workspace, intent=intent, binding_store=bindings, event_store=events
    )
    if decision.action is not LocalLaunchReuseAction.START_NEW:
        raise LocalLaunchStartupError(
            f"local launch is not creatable: {decision.action.value}: {decision.reason}"
        )
    owner = LocalLaunchOwner.create(
        intent=intent,
        workspace=workspace,
        asserted_by="contributor-dev-supervisor",
        store=events,
        binding_store=bindings,
        owner_pid=os.getpid(),
    )
    LocalLaunchAccessStore.from_database(database_path).publish(
        launch_id=_launch_id(owner),
        owner_generation=owner.identity.owner_generation,
        token=token,
    )
    processes: list[subprocess.Popen[str]] = []
    pgid: int | None = None
    logs: collections.deque[str] = collections.deque(maxlen=80)
    try:
        child_env = dict(env)
        child_env[local_access_protocol.LOCAL_ACCESS_ENV] = token
        launch_context = owner.install_runtime_context(
            backend_generation=BACKEND_GENERATION,
            environ=child_env,
        )
        try:
            transported_context = LocalLaunchRuntimeContext.from_environment(child_env)
        except ValueError as exc:
            raise LocalLaunchStartupError(
                "local launch runtime context transport is incomplete"
            ) from exc
        if transported_context != launch_context:
            raise LocalLaunchStartupError("local launch runtime context transport diverged")
        startup_deadline = now() + plan.startup_timeout_seconds
        for service in plan.services:
            process = _spawn_registered_service(
                root=root,
                owner=owner,
                service=service,
                child_env=child_env,
                pgid=pgid,
                deadline=startup_deadline,
                now=now,
                log_tail=logs,
            )
            if pgid is None:
                pgid = process.pid
            processes.append(process)
        assert pgid is not None
        wait_for_services_ready(
            owner=owner,
            plan=plan,
            processes=tuple(processes),
            now=now,
            sleep=sleep,
            probe=probe,
            deadline=startup_deadline,
            log_tail=logs,
        )
        return RunningLocalLaunch(
            owner=owner, token=token, processes=tuple(processes), process_group=pgid, log_tail=logs
        )
    except BaseException:
        if pgid is not None:
            stop_process_group(pgid, tuple(processes))
        try:
            owner.finish_owned_launch()
        except ValueError:
            pass
        raise


@dataclass(slots=True)
class InProcessReadinessMonitor:
    """Readiness worker for a runtime whose server owns the current process.

    The supervisor module owns the startup budget and readiness transition. The
    runtime owner only starts/cancels this worker around its blocking server call.
    """

    thread: threading.Thread
    cancel_event: threading.Event

    def cancel(self) -> None:
        self.cancel_event.set()
        self.thread.join(timeout=1.0)
        if self.thread.is_alive():
            raise RuntimeError("local readiness worker did not terminate")


def start_inprocess_readiness_monitor(
    *,
    owner: LocalLaunchOwner,
    plan: LocalLaunchPlan,
    on_ready: Callable[[], None] | None = None,
    on_failure: Callable[[], None] | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    probe: Callable[[LocalServicePlan], tuple[bool, str]] = probe_http,
) -> InProcessReadinessMonitor:
    """Start the single-budget readiness coordinator for an in-process server."""
    if len(plan.services) != 1:
        raise ValueError("in-process readiness requires exactly one required service")
    cancel = threading.Event()

    def observe() -> None:
        service = plan.services[0]
        deadline = now() + plan.startup_timeout_seconds
        last = "not probed"
        while not cancel.is_set():
            ok, last = probe(service)
            if ok:
                try:
                    owner.bound(
                        service.service, service.generation, host=service.host, port=service.port
                    )
                    owner.ready(service.service, service.generation)
                    owner.launch_ready()
                except ValueError:
                    return
                if on_ready is not None:
                    on_ready()
                return
            if now() >= deadline:
                reason = (
                    f"{plan.startup_timeout_seconds:g} second startup budget exhausted; "
                    f"{service.service}={last}"
                )
                try:
                    owner.service_failed(
                        service.service,
                        service.generation,
                        code="readiness_timeout",
                        reason=reason,
                        retryable=True,
                        failure_kind="readiness",
                    )
                    owner.launch_failed(
                        code="readiness_timeout",
                        reason=reason,
                        retryable=True,
                        failure_kind="readiness",
                    )
                except ValueError:
                    pass
                if on_failure is not None:
                    on_failure()
                return
            sleep(min(0.1, max(0.0, deadline - now())))

    thread = threading.Thread(
        target=observe,
        name=f"oh-goon-launch-readiness-{owner.identity.launch_id}",
        daemon=True,
    )
    thread.start()
    return InProcessReadinessMonitor(thread=thread, cancel_event=cancel)
