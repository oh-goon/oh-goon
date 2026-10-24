from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from oh_goon.api.local_access import new_local_access_token
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.process_birth import observe_process_liveness
from oh_goon.integrations._fleet_plan_cgroup_resources import NESTED_CGROUP_DELEGATED_ENV
from oh_goon.repository_tooling.acceptance.dev_runtime_repositories import (
    prepare_runtime_acceptance_repositories,
)
from oh_goon.repository_tooling.acceptance.local_delegation import (
    needs_systemd_delegation,
    run_under_systemd_delegation,
)
from oh_goon.repository_tooling.acceptance.local_runtime import (
    exercise_operator_parallel_acceptance,
    prepare_local_runtime_environment,
    prepare_verification_local_runtime_environment,
)
from oh_goon.repository_tooling.acceptance.product import exercise_location_lifecycle
from oh_goon.repository_tooling.acceptance.shared_plan import exercise_shared_plan
from oh_goon.repository_tooling.acceptance.timing import RuntimeAcceptanceTiming
from oh_goon.repository_tooling.local_access_http import authorized_request
from oh_goon.repository_tooling.release.runtime_journey import (
    cancel_acceptance_goon,
    run_acceptance_goon,
    start_acceptance_goon,
    team_run_request_payload,
    wait_for_acceptance_run_status,
    wait_for_interruptible_step,
)
from oh_goon.runtime.local_launch_plan import contributor_dev_plan
from oh_goon.runtime.local_launch_supervisor import (
    LocalLaunchStartupError,
    RunningLocalLaunch,
    start_contributor_launch,
)

ROOT = Path(__file__).resolve().parents[4]
HOST = "127.0.0.1"
BACKEND_PORT = 8787
FRONTEND_PORT = 5173


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def _wait_ports_free(*, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_free(BACKEND_PORT) and _port_is_free(FRONTEND_PORT):
            return True
        time.sleep(0.1)
    return False


def _http_status(
    url: str,
    *,
    token: str | None = None,
    cookie: str | None = None,
    method: str = "GET",
    timeout: float = 1.0,
) -> int:
    headers: dict[str, str] = {}
    if token is not None:
        headers["X-Oh-Goon-Local-Access"] = token
    if cookie is not None:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def _request_json(url: str, *, token: str) -> object:
    request = authorized_request(url, access_token=token)
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json_expect_error(
    url: str,
    *,
    token: str,
    payload: dict[str, object] | None,
    expected_status: int,
    expected_code: str,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else None
    request = authorized_request(url, access_token=token, data=data, method="POST", headers=headers)
    try:
        urllib.request.urlopen(request, timeout=5.0)
    except urllib.error.HTTPError as exc:
        body: object = json.loads(exc.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise RuntimeError(f"expected a JSON error object from {url}") from exc
        if exc.code != expected_status or body.get("code") != expected_code:
            raise RuntimeError(
                f"expected HTTP {expected_status}/{expected_code} from {url}, "
                f"observed HTTP {exc.code}: {body!r}"
            ) from exc
        return body
    raise RuntimeError(
        f"expected HTTP {expected_status}/{expected_code} from {url}, request unexpectedly succeeded"
    )


def _exchange_cookie(base_url: str, token: str) -> str:
    request = urllib.request.Request(
        f"{base_url}/api/local-session",
        headers={"X-Oh-Goon-Local-Access": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2.0) as response:
        if response.status != 204:
            raise RuntimeError(
                f"local-session exchange returned HTTP {response.status}, expected 204"
            )
        raw_cookie: str = response.headers.get("Set-Cookie", "")
    if "HttpOnly" not in raw_cookie or "SameSite=strict" not in raw_cookie:
        raise RuntimeError(
            "local-session exchange did not return the expected HttpOnly SameSite=strict cookie"
        )
    pair = raw_cookie.split(";", 1)[0].strip()
    if "=" not in pair:
        raise RuntimeError("local-session exchange did not return a usable session cookie")
    return pair


def _wait_status(
    url: str, expected: int, *, token: str | None = None, timeout: float = 30.0
) -> None:
    deadline = time.monotonic() + timeout
    last: int | str = "not attempted"
    while time.monotonic() < deadline:
        try:
            last = _http_status(url, token=token)
            if last == expected:
                return
        except (OSError, urllib.error.URLError) as exc:
            last = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not reach HTTP {expected}; last result: {last}")


def _pid_is_alive(pid: int) -> bool:
    observed = observe_process_liveness(pid)
    if observed is not None:
        return observed
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_pid_dead(pid: int, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_is_alive(pid)


def _launch_dev(root: Path, env: dict[str, str]) -> RunningLocalLaunch:
    return start_contributor_launch(
        root=root, env=env, plan=contributor_dev_plan(root), token=new_local_access_token()
    )


def _exercise_access_boundaries(*, frontend_base: str, token: str, issues: list[str]) -> None:
    _wait_status(f"http://{HOST}:{BACKEND_PORT}/healthz", 204)
    _wait_status(f"http://{HOST}:{BACKEND_PORT}/api/health", 401)
    _wait_status(f"http://{HOST}:{BACKEND_PORT}/api/health", 200, token=token)
    wrong = _http_status(f"http://{HOST}:{BACKEND_PORT}/api/health", token="wrong-token")
    if wrong != 401:
        issues.append(f"wrong local-access token returned HTTP {wrong}, expected 401")
    _wait_status(f"{frontend_base}/", 200)
    _wait_status(f"{frontend_base}/api/health", 200, token=token)
    cookie = _exchange_cookie(frontend_base, token)
    cookie_status = _http_status(f"{frontend_base}/api/health", cookie=cookie)
    if cookie_status != 200:
        issues.append(f"browser-session cookie returned HTTP {cookie_status}, expected 200")


def _verify_restart_persistence(
    *,
    frontend_base: str,
    token: str,
    repositories: Path,
    historical_run_id: str,
) -> None:
    locations = _request_json(f"{frontend_base}/api/locations", token=token)
    configured = str(repositories.resolve())
    if not isinstance(locations, dict) or configured not in locations.get("locations", []):
        raise RuntimeError("real-world dev restart did not preserve configured repository location")
    historical = _request_json(f"{frontend_base}/api/fleet/runs/{historical_run_id}", token=token)
    if "acceptance-step" not in json.dumps(historical):
        raise RuntimeError("real-world dev restart did not preserve historical Game Tape")
    shared = _request_json(
        f"{frontend_base}/api/league/shared-plans/acceptance-shared", token=token
    )
    if not isinstance(shared, dict) or shared.get("name") != "acceptance-shared":
        raise RuntimeError("real-world dev restart did not preserve Shared Plan state")


def _verify_parallel_cancel_restart(
    frontend_base: str, token: str, cancelled_id: str, success_id: str
) -> None:
    cancelled = _request_json(f"{frontend_base}/api/fleet/runs/{cancelled_id}", token=token)
    cancelled_run = cancelled.get("run") if isinstance(cancelled, dict) else None
    if (
        not isinstance(cancelled, dict)
        or not isinstance(cancelled_run, dict)
        or cancelled_run.get("status") != "CANCELLED"
        or cancelled.get("outcome") != "CANCELLED"
    ):
        raise RuntimeError("restart did not preserve the physically cancelled parallel Shared Plan")
    success = _request_json(f"{frontend_base}/api/fleet/runs/{success_id}", token=token)
    success_run = success.get("run") if isinstance(success, dict) else None
    if (
        not isinstance(success, dict)
        or not isinstance(success_run, dict)
        or success_run.get("status") != "COMPLETED"
        or success.get("outcome") != "SUCCESS"
    ):
        raise RuntimeError(
            "restart did not preserve the successful post-cancel parallel Shared Plan"
        )


def _verify_parallel_repair_restart(
    frontend_base: str, token: str, failed_id: str, repaired_id: str
) -> None:
    failed = _request_json(f"{frontend_base}/api/fleet/runs/{failed_id}", token=token)
    failed_run = failed.get("run") if isinstance(failed, dict) else None
    if not isinstance(failed_run, dict) or failed_run.get("status") != "FAILED":
        raise RuntimeError(
            "restart did not preserve the repository-attributed failed parallel Shared Plan"
        )
    rendered = json.dumps(failed)
    if "parallel-failure-left" not in rendered or "parallel-failure-right" not in rendered:
        raise RuntimeError(
            "restart lost per-repository attribution for failed parallel Shared Plan"
        )
    repaired = _request_json(f"{frontend_base}/api/fleet/runs/{repaired_id}", token=token)
    repaired_run = repaired.get("run") if isinstance(repaired, dict) else None
    if (
        not isinstance(repaired, dict)
        or not isinstance(repaired_run, dict)
        or repaired_run.get("status") != "COMPLETED"
        or repaired.get("outcome") != "SUCCESS"
    ):
        raise RuntimeError("restart did not preserve the repaired successful parallel Shared Plan")


def _verify_parallel_stale_restart(frontend_base: str, token: str, recovered_id: str) -> None:
    recovered = _request_json(f"{frontend_base}/api/fleet/runs/{recovered_id}", token=token)
    recovered_run = recovered.get("run") if isinstance(recovered, dict) else None
    if (
        not isinstance(recovered, dict)
        or not isinstance(recovered_run, dict)
        or recovered_run.get("status") != "COMPLETED"
        or recovered.get("outcome") != "SUCCESS"
    ):
        raise RuntimeError("restart did not preserve the refreshed post-stale parallel Shared Plan")


def _verify_restart_history(
    rendered_history: str,
    *,
    required_ids: tuple[str | None, ...],
    rejected_id: str | None,
) -> None:
    if any(run_id is not None and run_id not in rendered_history for run_id in required_ids):
        raise RuntimeError(
            "real-world dev restart history did not retain success/cancelled/competing/interrupted/parallel-failed/parallel-repaired/stale-recovered-without-stale-admission/post-restart runs"
        )
    if rejected_id is not None and rejected_id in rendered_history:
        raise RuntimeError(
            "real-world dev restart history did not retain success/cancelled/competing/interrupted/parallel-failed/parallel-repaired/stale-recovered-without-stale-admission/post-restart runs"
        )


def _verify_primary_restart_state(
    frontend_base: str, token: str, cancelled_run_id: str, interrupted_run_id: str
) -> None:
    cancelled = _request_json(f"{frontend_base}/api/fleet/runs/{cancelled_run_id}", token=token)
    cancelled_run = cancelled.get("run") if isinstance(cancelled, dict) else None
    if (
        not isinstance(cancelled, dict)
        or not isinstance(cancelled_run, dict)
        or cancelled_run.get("status") != "CANCELLED"
        or cancelled.get("outcome") != "CANCELLED"
    ):
        raise RuntimeError(
            f"restart did not preserve the physically cancelled real Run: {cancelled!r}"
        )
    interrupted = _request_json(f"{frontend_base}/api/fleet/runs/{interrupted_run_id}", token=token)
    run = interrupted.get("run") if isinstance(interrupted, dict) else None
    if not isinstance(run, dict) or run.get("status") != "FAILED":
        raise RuntimeError(
            f"crash-interrupted real Run was not terminalized FAILED: {interrupted!r}"
        )
    failure = run.get("failure")
    failure_code = failure.get("code") if isinstance(failure, dict) else None
    if failure_code not in {"REPOSITORY_EFFECT_OUTCOME_UNKNOWN", "RUN_INTERRUPTED_BY_RESTART"}:
        raise RuntimeError(
            f"crash-interrupted real Run lost canonical restart failure evidence: {interrupted!r}"
        )
    fleet = _request_json(f"{frontend_base}/api/fleet", token=token)
    if not isinstance(fleet, dict) or fleet.get("active_fleet_run") is not None:
        raise RuntimeError("crash/restart left a ghost active Fleet Run")


def _verify_crash_restart_journey(
    *,
    root: Path,
    env: dict[str, str],
    frontend_base: str,
    repositories: Path,
    repository: Path,
    issues: list[str],
    first_run_id: str,
    cancelled_run_id: str,
    competing_run_id: str,
    interrupted_run_id: str,
    parallel_cancelled_run_id: str | None,
    parallel_success_run_id: str | None,
    parallel_failed_run_id: str | None,
    parallel_repaired_run_id: str | None,
    parallel_stale_rejected_run_id: str | None,
    parallel_stale_recovered_run_id: str | None,
) -> None:
    """Verify durable run truth and safe execution after abrupt dev-owner loss."""
    running2 = _launch_dev(root, env)
    token2 = running2.token
    try:
        _exercise_access_boundaries(frontend_base=frontend_base, token=token2, issues=issues)
        _verify_restart_persistence(
            frontend_base=frontend_base,
            token=token2,
            repositories=repositories,
            historical_run_id=first_run_id,
        )
        if parallel_cancelled_run_id is not None and parallel_success_run_id is not None:
            _verify_parallel_cancel_restart(
                frontend_base, token2, parallel_cancelled_run_id, parallel_success_run_id
            )
        if parallel_failed_run_id is not None and parallel_repaired_run_id is not None:
            _verify_parallel_repair_restart(
                frontend_base, token2, parallel_failed_run_id, parallel_repaired_run_id
            )
        if parallel_stale_recovered_run_id is not None:
            _verify_parallel_stale_restart(frontend_base, token2, parallel_stale_recovered_run_id)

        _verify_primary_restart_state(frontend_base, token2, cancelled_run_id, interrupted_run_id)
        second_run_id = run_acceptance_goon(
            base_url=frontend_base,
            server_repository_path=str(repository.resolve()),
            access_token=token2,
        )
        if second_run_id == first_run_id:
            raise RuntimeError("post-restart execution reused the historical Fleet Run identity")
        history = _request_json(f"{frontend_base}/api/fleet/runs?limit=20", token=token2)
        rendered_history = json.dumps(history)
        _verify_restart_history(
            rendered_history,
            required_ids=(
                first_run_id,
                cancelled_run_id,
                competing_run_id,
                interrupted_run_id,
                second_run_id,
                parallel_cancelled_run_id,
                parallel_success_run_id,
                parallel_failed_run_id,
                parallel_repaired_run_id,
                parallel_stale_recovered_run_id,
            ),
            rejected_id=parallel_stale_rejected_run_id,
        )
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        issues.append(str(exc))
    finally:
        running2.stop()


def _post_crash_cleanup_issues(interrupted_pid: int | None) -> tuple[str, ...]:
    issues: list[str] = []
    if interrupted_pid is not None and not _wait_pid_dead(interrupted_pid):
        issues.append("hard crash left the physically running repository Step process alive")
    if not _wait_ports_free():
        issues.append("crashed make dev left 127.0.0.1:5173 or :8787 occupied")
    return tuple(issues)


def _missing_runtime_identity(*values: object | None) -> bool:
    return any(value is None for value in values)


def accept_local_runtime_verification(root: Path = ROOT) -> tuple[str, ...]:
    """Prove APP local-runtime startup/access/teardown without strict Fleet authority."""

    occupied = [str(port) for port in (FRONTEND_PORT, BACKEND_PORT) if not _port_is_free(port)]
    if occupied:
        return (
            "local-runtime verification requires free loopback ports before startup: "
            + ", ".join(occupied),
        )

    with tempfile.TemporaryDirectory(prefix="oh-goon-local-runtime-verification-") as temporary:
        temp = Path(temporary)
        env = prepare_verification_local_runtime_environment(
            temp=temp,
            state=temp / "state",
            config=temp / "config",
        )
        frontend_base = f"http://{HOST}:{FRONTEND_PORT}"
        issues: list[str] = []
        running: RunningLocalLaunch | None = None
        try:
            running = _launch_dev(root, env)
            _exercise_access_boundaries(
                frontend_base=frontend_base, token=running.token, issues=issues
            )
        except (LocalLaunchStartupError, OSError, RuntimeError, urllib.error.URLError) as exc:
            issues.append(str(exc))
            if running is not None:
                tail = running.diagnostic_tail()
                issues.extend(
                    ("bounded redacted local-runtime tail:\n" + "\n".join(tail),) * bool(tail)
                )
        finally:
            if running is not None:
                running.stop()
        if not _wait_ports_free():
            issues.append(
                "local-runtime verification teardown left 127.0.0.1:5173 or :8787 occupied"
            )
        return tuple(issues)


def accept_dev_runtime(
    root: Path = ROOT, *, timing: RuntimeAcceptanceTiming | None = None
) -> tuple[str, ...]:
    timing = RuntimeAcceptanceTiming.resolve(os.environ) if timing is None else timing
    step_start_timeout = timing.step_start_timeout_seconds
    interruptible_step_seconds = timing.interruptible_step_seconds
    occupied = [str(port) for port in (FRONTEND_PORT, BACKEND_PORT) if not _port_is_free(port)]
    if occupied:
        return (
            f"local-access runtime acceptance requires free loopback ports before startup: {', '.join(occupied)}",
        )

    with tempfile.TemporaryDirectory(prefix="oh-goon-dev-runtime-") as temporary:
        temp = Path(temporary)
        state = temp / "state"
        config = temp / "config"
        (
            repositories,
            repository,
            cancelled_repository,
            competing_repository,
            interrupted_repository,
        ) = prepare_runtime_acceptance_repositories(
            temp, interruptible_step_seconds=interruptible_step_seconds
        )
        env, operator_executor_configured, executor_issue = prepare_local_runtime_environment(
            temp=temp, state=state, config=config
        )
        if executor_issue:
            return (executor_issue,)
        frontend_base = f"http://{HOST}:{FRONTEND_PORT}"
        issues: list[str] = []
        first_run_id: str | None = None
        cancelled_run_id: str | None = None
        cancelled_pid: int | None = None
        competing_run_id: str | None = None
        interrupted_run_id: str | None = None
        interrupted_pid: int | None = None
        parallel_cancelled_run_id: str | None = None
        parallel_success_run_id: str | None = None
        parallel_failed_run_id: str | None = None
        parallel_repaired_run_id: str | None = None
        parallel_stale_rejected_run_id: str | None = None
        parallel_stale_recovered_run_id: str | None = None

        running = _launch_dev(root, env)
        token = running.token
        try:
            _exercise_access_boundaries(frontend_base=frontend_base, token=token, issues=issues)
            first_run_id = run_acceptance_goon(
                base_url=frontend_base,
                server_repository_path=str(repository.resolve()),
                access_token=token,
            )
            exercise_location_lifecycle(
                base_url=frontend_base,
                configured_root=repositories,
                run_id=first_run_id,
                access_token=token,
            )
            exercise_shared_plan(frontend_base, repositories, access_token=token)
            (
                parallel_cancelled_run_id,
                parallel_success_run_id,
                parallel_failed_run_id,
                parallel_repaired_run_id,
                parallel_stale_rejected_run_id,
                parallel_stale_recovered_run_id,
            ) = exercise_operator_parallel_acceptance(
                enabled=operator_executor_configured,
                environment=env,
                frontend_base=frontend_base,
                repositories=repositories,
                access_token=token,
            )

            # Real cancellation proof: cancellation is requested through the public API
            # only after both durable STEP_STARTED evidence and the physical child PID
            # prove repository work is actually running.  The process-group owner must
            # stop that child and settle one canonical CANCELLED Run without a restart.
            cancelled_run_id = start_acceptance_goon(
                base_url=frontend_base,
                server_repository_path=str(cancelled_repository.resolve()),
                access_token=token,
            )
            cancelled_pid = wait_for_interruptible_step(
                base_url=frontend_base,
                run_id=cancelled_run_id,
                repository=cancelled_repository,
                access_token=token,
                timeout_seconds=step_start_timeout,
            )
            _post_json_expect_error(
                f"{frontend_base}/api/fleet/runs/{cancelled_run_id}-wrong/cancel",
                token=token,
                payload=None,
                expected_status=404,
                expected_code="RUN_NOT_FOUND",
            )
            if not _pid_is_alive(cancelled_pid):
                raise RuntimeError("wrong-run cancellation killed the active physical Step child")
            competing_request_id = "competing-while-active"
            _post_json_expect_error(
                f"{frontend_base}/api/league/team-runs",
                token=token,
                payload=team_run_request_payload(
                    frontend_base, str(competing_repository.resolve()), token, competing_request_id
                ),
                expected_status=409,
                expected_code="EXECUTION_BUSY",
            )
            if not _pid_is_alive(cancelled_pid):
                raise RuntimeError(
                    "competing repository admission disturbed the active physical Step"
                )
            if (cancelled_repository / "acceptance-step.finished").exists():
                raise RuntimeError(
                    "active Step completed while testing conflicting admission isolation"
                )
            cancel_result = cancel_acceptance_goon(
                base_url=frontend_base, run_id=cancelled_run_id, access_token=token
            )
            if cancel_result.get("status") not in {"CANCELLING", "CANCELLED"}:
                raise RuntimeError(
                    f"real-world cancellation returned unexpected state: {cancel_result!r}"
                )
            cancelled_detail = wait_for_acceptance_run_status(
                base_url=frontend_base,
                run_id=cancelled_run_id,
                access_token=token,
                expected_status="CANCELLED",
            )
            if cancelled_detail.get("outcome") != "CANCELLED":
                raise RuntimeError(
                    f"real-world cancelled Run lost CANCELLED outcome: {cancelled_detail!r}"
                )
            if not _wait_pid_dead(cancelled_pid):
                raise RuntimeError("real-world cancellation left the physical Step child alive")
            if (cancelled_repository / "acceptance-step.finished").exists():
                raise RuntimeError(
                    "real-world cancelled Step reached its forbidden completion marker"
                )
            repeated_cancel = cancel_acceptance_goon(
                base_url=frontend_base, run_id=cancelled_run_id, access_token=token
            )
            if repeated_cancel.get("status") != "CANCELLED":
                raise RuntimeError(
                    "real-world repeated cancellation was not idempotently CANCELLED"
                )
            after_cancel = _request_json(f"{frontend_base}/api/fleet", token=token)
            if (
                not isinstance(after_cancel, dict)
                or after_cancel.get("active_fleet_run") is not None
            ):
                raise RuntimeError("real-world cancellation left a ghost active Fleet Run")
            competing_run_id = run_acceptance_goon(
                base_url=frontend_base,
                server_repository_path=str(competing_repository.resolve()),
                access_token=token,
            )

            interrupted_run_id = start_acceptance_goon(
                base_url=frontend_base,
                server_repository_path=str(interrupted_repository.resolve()),
                access_token=token,
            )
            interrupted_pid = wait_for_interruptible_step(
                base_url=frontend_base,
                run_id=interrupted_run_id,
                repository=interrupted_repository,
                access_token=token,
                timeout_seconds=step_start_timeout,
            )
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            issues.append(str(exc))
            tail = running.diagnostic_tail()
            issues.extend(
                ("bounded redacted local-runtime tail:\n" + "\n".join(tail),) * bool(tail)
            )
        finally:
            if issues or _missing_runtime_identity(
                first_run_id,
                cancelled_run_id,
                cancelled_pid,
                competing_run_id,
                interrupted_run_id,
                interrupted_pid,
            ):
                running.stop()
            else:
                running.crash()

        issues.extend(_post_crash_cleanup_issues(interrupted_pid))
        if any("occupied" in issue for issue in issues):
            return tuple(issues)
        if (
            issues
            or first_run_id is None
            or cancelled_run_id is None
            or competing_run_id is None
            or interrupted_run_id is None
        ):
            return tuple(issues)

        # A hard process loss during a physical Step must be recoverable without EXIT cleanup.
        _verify_crash_restart_journey(
            root=root,
            env=env,
            frontend_base=frontend_base,
            repositories=repositories,
            repository=repository,
            issues=issues,
            first_run_id=first_run_id,
            cancelled_run_id=cancelled_run_id,
            competing_run_id=competing_run_id,
            interrupted_run_id=interrupted_run_id,
            parallel_cancelled_run_id=parallel_cancelled_run_id,
            parallel_success_run_id=parallel_success_run_id,
            parallel_failed_run_id=parallel_failed_run_id,
            parallel_repaired_run_id=parallel_repaired_run_id,
            parallel_stale_rejected_run_id=parallel_stale_rejected_run_id,
            parallel_stale_recovered_run_id=parallel_stale_recovered_run_id,
        )

        if not _wait_ports_free():
            issues.append("restarted make dev teardown left 127.0.0.1:5173 or :8787 occupied")
        return tuple(issues)


def main(argv: tuple[str, ...] | None = None) -> int:
    if argv is None:
        import sys

        argv = tuple(sys.argv[1:])
    if argv not in ((), ("strict",), ("verification",)):
        print("usage: oh-goon-dev-runtime-acceptance [strict|verification]")
        return 2
    mode = "strict" if not argv else argv[0]
    if mode == "verification":
        try:
            issues = accept_local_runtime_verification(ROOT)
        except Exception as exc:
            issues = (f"{type(exc).__name__}: {exc}",)
        if issues:
            print("Local-runtime APP verification failed:")
            for issue in issues:
                print(f"  - {issue}")
            return 1
        print(
            "Local-runtime APP verification passed: canonical backend + frontend startup -> "
            "local-access auth/proxy boundaries -> deterministic teardown; no strict executor, "
            "cgroup, or systemd delegation authority was acquired."
        )
        return 0

    environment = dict(os.environ)
    try:
        timing = RuntimeAcceptanceTiming.resolve(environment)
        if needs_systemd_delegation(environment):
            return run_under_systemd_delegation(ROOT, environment=environment, timing=timing)
        # Only the process that actually owns execution reports effective authority.
        # The pre-delegation resolver transports it without presenting itself as executor.
        print(timing.summary())
    except (FleetPlanInvalid, OSError) as exc:
        if environment.get(NESTED_CGROUP_DELEGATED_ENV) == "1":
            print("Local-access dev runtime acceptance failed:")
            print(f"  - nested delegated cgroup-v2 authority is invalid: {exc}")
            print("  - systemd fallback is forbidden inside nested strict execution")
            return 1
        raise
    try:
        issues = accept_dev_runtime(ROOT, timing=timing)
    except Exception as exc:
        issues = (f"{type(exc).__name__}: {exc}",)
    if issues:
        print("Local-access dev runtime acceptance failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print(
        "Local-access dev runtime acceptance passed: auth + Vite proxy -> real Repo/Goon/Game Tape -> Shared Plan -> optional strict parallel Shared Plan physical fan-out/cancel/failure-isolation/repair-rerun/stale-preview-fencing when an executor is configured -> wrong-run cancellation isolation + competing repository busy rejection -> physical live-Step cancellation + child termination -> lease release + competing repository execution -> active-Step hard crash/restart -> durable cancellation/crash recovery -> post-restart execution -> deterministic teardown verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
