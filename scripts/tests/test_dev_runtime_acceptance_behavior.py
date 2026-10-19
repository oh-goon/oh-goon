from __future__ import annotations

from email.message import Message

from typing import Self

import os
import time
import urllib.request

from pathlib import Path
from typing import Literal

import io
import urllib.error

import pytest

from oh_goon.repository_tooling import dev_runtime_acceptance as acceptance
from oh_goon.repository_tooling.acceptance import dev_runtime_repositories


class _Response:
    def __init__(
        self, *, status: int = 200, body: bytes = b"{}", cookie: str = ""
    ) -> None:
        self.status = status
        self._body = body
        self.headers = {"Set-Cookie": cookie} if cookie else {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
        return False

    def read(self) -> bytes:
        return self._body


class _Reader:
    def __init__(self) -> None:
        self.join_calls: list[float | None] = []

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)


class _Running:
    def __init__(self, token: str = "token") -> None:
        self.token = token
        self.stop_calls = 0
        self.crash_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1

    def crash(self) -> None:
        self.crash_calls += 1

    def diagnostic_tail(self) -> tuple[str, ...]:
        return ()


class _Process:
    def __init__(
        self,
        *,
        poll_result: int | None = None,
        returncode: int | None = None,
        pid: int = 1234,
    ) -> None:
        self._poll_result = poll_result
        self.returncode = returncode
        self.pid = pid
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self._poll_result

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        self._poll_result = 0
        return 0


def _http_error(code: int, body: dict[str, object]) -> urllib.error.HTTPError:
    import json

    return urllib.error.HTTPError(
        "http://example.test",
        code,
        "error",
        hdrs=Message(),
        fp=io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def test_http_helpers_preserve_headers_status_and_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[urllib.request.Request, float]] = []

    def open_ok(request: urllib.request.Request, timeout: float) -> _Response:
        observed.append((request, timeout))
        return _Response(status=202, body=b'{"ready": true}')

    monkeypatch.setattr(urllib.request, "urlopen", open_ok)
    assert (
        acceptance._http_status(
            "http://example.test/check",
            token="token",
            cookie="sid=abc",
            method="POST",
            timeout=3.0,
        )
        == 202
    )
    request = observed[-1][0]
    assert request.get_header("X-oh-goon-local-access") == "token"
    assert request.get_header("Cookie") == "sid=abc"
    assert request.get_method() == "POST"

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: _Response(body=b'{"value": 7}'),
    )
    assert acceptance._request_json("http://example.test/data", token="secret") == {
        "value": 7
    }

    def open_error(request: urllib.request.Request, timeout: float) -> None:
        raise _http_error(401, {"code": "NOPE"})

    monkeypatch.setattr(urllib.request, "urlopen", open_error)
    assert acceptance._http_status("http://example.test/check") == 401


def test_expected_error_helper_accepts_exact_error_and_rejects_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exact(request: urllib.request.Request, timeout: float) -> None:
        raise _http_error(409, {"code": "EXECUTION_BUSY"})

    monkeypatch.setattr(urllib.request, "urlopen", exact)
    assert acceptance._post_json_expect_error(
        "http://example.test/run",
        token="token",
        payload={"repository": "/repo"},
        expected_status=409,
        expected_code="EXECUTION_BUSY",
    ) == {"code": "EXECUTION_BUSY"}

    with pytest.raises(RuntimeError, match="expected HTTP 404/RUN_NOT_FOUND"):
        acceptance._post_json_expect_error(
            "http://example.test/run",
            token="token",
            payload=None,
            expected_status=404,
            expected_code="RUN_NOT_FOUND",
        )

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: _Response(status=204),
    )
    with pytest.raises(RuntimeError, match="request unexpectedly succeeded"):
        acceptance._post_json_expect_error(
            "http://example.test/run",
            token="token",
            payload=None,
            expected_status=404,
            expected_code="RUN_NOT_FOUND",
        )


@pytest.mark.parametrize(
    ("status", "cookie", "message"),
    [
        (200, "sid=abc; HttpOnly; SameSite=strict", "returned HTTP 200"),
        (204, "sid=abc; SameSite=strict", "expected HttpOnly"),
        (204, "sid=abc; HttpOnly", "expected HttpOnly"),
        (204, "broken; HttpOnly; SameSite=strict", "usable session cookie"),
    ],
)
def test_cookie_exchange_rejects_invalid_session_contract(
    monkeypatch: pytest.MonkeyPatch, status: int, cookie: str, message: str
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: _Response(status=status, cookie=cookie),
    )
    with pytest.raises(RuntimeError, match=message):
        acceptance._exchange_cookie("http://frontend", "token")


def test_cookie_exchange_returns_only_cookie_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: _Response(
            status=204, cookie="sid=abc; Path=/; HttpOnly; SameSite=strict"
        ),
    )
    assert acceptance._exchange_cookie("http://frontend", "token") == "sid=abc"


def test_wait_helpers_cover_success_retry_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter([503, 200])
    monkeypatch.setattr(
        acceptance, "_http_status", lambda url, token=None: next(statuses)
    )
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    acceptance._wait_status("http://example.test", 200, timeout=1.0)

    ticks = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.2))
    monkeypatch.setattr(
        acceptance,
        "_http_status",
        lambda url, token=None: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    with pytest.raises(RuntimeError, match="last result: <urlopen error down>"):
        acceptance._wait_status("http://example.test", 200, timeout=0.15)

    calls = iter([False, True, True])
    monkeypatch.setattr(acceptance, "_port_is_free", lambda port: next(calls))
    ticks = iter([0.0, 0.05, 0.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.1))
    assert acceptance._wait_ports_free(timeout=0.15) is True

    monkeypatch.setattr(acceptance, "_port_is_free", lambda port: False)
    ticks = iter([0.0, 0.2])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.2))
    assert acceptance._wait_ports_free(timeout=0.1) is False


def test_pid_liveness_and_wait_dead_cover_observer_and_os_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acceptance, "observe_process_liveness", lambda pid: True)
    assert acceptance._pid_is_alive(1) is True
    monkeypatch.setattr(acceptance, "observe_process_liveness", lambda pid: False)
    assert acceptance._pid_is_alive(1) is False

    monkeypatch.setattr(acceptance, "observe_process_liveness", lambda pid: None)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    assert acceptance._pid_is_alive(1) is True

    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert acceptance._pid_is_alive(1) is False

    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: (_ for _ in ()).throw(PermissionError()),
    )
    assert acceptance._pid_is_alive(1) is True

    alive = iter([True, False])
    monkeypatch.setattr(acceptance, "_pid_is_alive", lambda pid: next(alive, False))
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    ticks = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.2))
    assert acceptance._wait_pid_dead(1, timeout=0.15) is True


def test_access_boundaries_collect_wrong_token_and_cookie_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[tuple[str, int, str | None]] = []
    monkeypatch.setattr(
        acceptance,
        "_wait_status",
        lambda url, expected, token=None: waits.append((url, expected, token)),
    )
    statuses = iter([403, 500])
    monkeypatch.setattr(
        acceptance, "_http_status", lambda *args, **kwargs: next(statuses)
    )
    monkeypatch.setattr(
        acceptance, "_exchange_cookie", lambda base_url, token: "sid=abc"
    )
    issues: list[str] = []

    acceptance._exercise_access_boundaries(
        frontend_base="http://frontend", token="token", issues=issues
    )

    assert len(waits) == 5
    assert "wrong local-access token returned HTTP 403" in issues[0]
    assert "browser-session cookie returned HTTP 500" in issues[1]


def test_restart_persistence_accepts_complete_state_and_rejects_each_missing_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repositories = tmp_path / "repositories"
    repositories.mkdir()
    good = [
        {"locations": [str(repositories.resolve())]},
        {"run": {"steps": ["acceptance-step"]}},
        {"name": "acceptance-shared"},
    ]
    monkeypatch.setattr(
        acceptance, "_request_json", lambda *args, **kwargs: good.pop(0)
    )
    acceptance._verify_restart_persistence(
        frontend_base="http://frontend",
        token="token",
        repositories=repositories,
        historical_run_id="run-1",
    )

    cases = [
        [{"locations": []}],
        [{"locations": [str(repositories.resolve())]}, {"run": {"steps": []}}],
        [
            {"locations": [str(repositories.resolve())]},
            {"run": {"steps": ["acceptance-step"]}},
            {"name": "wrong"},
        ],
    ]
    messages = [
        "configured repository location",
        "historical Game Tape",
        "Shared Plan state",
    ]
    for replies, message in zip(cases, messages, strict=True):
        monkeypatch.setattr(
            acceptance,
            "_request_json",
            lambda *args, _replies=replies, **kwargs: _replies.pop(0),
        )
        with pytest.raises(RuntimeError, match=message):
            acceptance._verify_restart_persistence(
                frontend_base="http://frontend",
                token="token",
                repositories=repositories,
                historical_run_id="run-1",
            )


def test_parallel_restart_validators_cover_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(
        [
            {"run": {"status": "CANCELLED"}, "outcome": "CANCELLED"},
            {"run": {"status": "COMPLETED"}, "outcome": "SUCCESS"},
        ]
    )
    monkeypatch.setattr(
        acceptance, "_request_json", lambda *args, **kwargs: next(replies)
    )
    acceptance._verify_parallel_cancel_restart(
        "http://frontend", "token", "cancel", "success"
    )

    monkeypatch.setattr(
        acceptance,
        "_request_json",
        lambda *args, **kwargs: {"run": {"status": "FAILED"}},
    )
    with pytest.raises(RuntimeError, match="cancelled parallel"):
        acceptance._verify_parallel_cancel_restart(
            "http://frontend", "token", "cancel", "success"
        )

    replies = iter(
        [
            {
                "run": {"status": "FAILED"},
                "evidence": ["parallel-failure-left", "parallel-failure-right"],
            },
            {"run": {"status": "COMPLETED"}, "outcome": "SUCCESS"},
        ]
    )
    monkeypatch.setattr(
        acceptance, "_request_json", lambda *args, **kwargs: next(replies)
    )
    acceptance._verify_parallel_repair_restart(
        "http://frontend", "token", "failed", "repaired"
    )

    monkeypatch.setattr(
        acceptance,
        "_request_json",
        lambda *args, **kwargs: {
            "run": {"status": "FAILED"},
            "evidence": ["parallel-failure-left"],
        },
    )
    with pytest.raises(RuntimeError, match="per-repository attribution"):
        acceptance._verify_parallel_repair_restart(
            "http://frontend", "token", "failed", "repaired"
        )

    monkeypatch.setattr(
        acceptance,
        "_request_json",
        lambda *args, **kwargs: {"run": {"status": "COMPLETED"}, "outcome": "SUCCESS"},
    )
    acceptance._verify_parallel_stale_restart("http://frontend", "token", "recovered")
    monkeypatch.setattr(
        acceptance,
        "_request_json",
        lambda *args, **kwargs: {"run": {"status": "FAILED"}},
    )
    with pytest.raises(RuntimeError, match="post-stale"):
        acceptance._verify_parallel_stale_restart(
            "http://frontend", "token", "recovered"
        )


def test_restart_history_and_primary_state_validate_durable_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acceptance._verify_restart_history(
        "run-a run-b",
        required_ids=("run-a", None, "run-b"),
        rejected_id="run-rejected",
    )
    with pytest.raises(RuntimeError, match="did not retain"):
        acceptance._verify_restart_history(
            "run-a", required_ids=("run-a", "run-b"), rejected_id=None
        )
    with pytest.raises(RuntimeError, match="did not retain"):
        acceptance._verify_restart_history(
            "run-a run-rejected", required_ids=("run-a",), rejected_id="run-rejected"
        )

    replies = iter(
        [
            {"run": {"status": "CANCELLED"}, "outcome": "CANCELLED"},
            {
                "run": {
                    "status": "FAILED",
                    "failure": {"code": "RUN_INTERRUPTED_BY_RESTART"},
                }
            },
            {"active_fleet_run": None},
        ]
    )
    monkeypatch.setattr(
        acceptance, "_request_json", lambda *args, **kwargs: next(replies)
    )
    acceptance._verify_primary_restart_state(
        "http://frontend", "token", "cancel", "interrupt"
    )

    monkeypatch.setattr(
        acceptance,
        "_request_json",
        lambda *args, **kwargs: {"run": {"status": "RUNNING"}},
    )
    with pytest.raises(RuntimeError, match="physically cancelled"):
        acceptance._verify_primary_restart_state(
            "http://frontend", "token", "cancel", "interrupt"
        )


def _invoke_restart_journey(
    tmp_path: Path, issues: list[str], *, parallel: bool
) -> None:
    acceptance._verify_crash_restart_journey(
        root=tmp_path,
        env={},
        frontend_base="http://frontend",
        repositories=tmp_path,
        repository=tmp_path / "repo",
        issues=issues,
        first_run_id="run-first",
        cancelled_run_id="run-cancel",
        competing_run_id="run-compete",
        interrupted_run_id="run-interrupt",
        parallel_cancelled_run_id="parallel-cancel" if parallel else None,
        parallel_success_run_id="parallel-success" if parallel else None,
        parallel_failed_run_id="parallel-failed" if parallel else None,
        parallel_repaired_run_id="parallel-repaired" if parallel else None,
        parallel_stale_rejected_run_id="parallel-stale-rejected" if parallel else None,
        parallel_stale_recovered_run_id="parallel-stale-recovered"
        if parallel
        else None,
    )


def test_crash_restart_journey_composes_all_durable_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    running = _Running("token-2")
    monkeypatch.setattr(acceptance, "_launch_dev", lambda root, env: running)
    calls: list[str] = []
    monkeypatch.setattr(
        acceptance,
        "_exercise_access_boundaries",
        lambda **kwargs: calls.append("access"),
    )
    monkeypatch.setattr(
        acceptance,
        "_verify_restart_persistence",
        lambda **kwargs: calls.append("persist"),
    )
    monkeypatch.setattr(
        acceptance,
        "_verify_parallel_cancel_restart",
        lambda *args: calls.append("cancel"),
    )
    monkeypatch.setattr(
        acceptance,
        "_verify_parallel_repair_restart",
        lambda *args: calls.append("repair"),
    )
    monkeypatch.setattr(
        acceptance,
        "_verify_parallel_stale_restart",
        lambda *args: calls.append("stale"),
    )
    monkeypatch.setattr(
        acceptance,
        "_verify_primary_restart_state",
        lambda *args: calls.append("primary"),
    )
    monkeypatch.setattr(
        acceptance, "run_acceptance_goon", lambda **kwargs: "run-second"
    )
    monkeypatch.setattr(
        acceptance,
        "_request_json",
        lambda *args, **kwargs: [
            {"fleet_run_id": name}
            for name in (
                "run-first",
                "run-cancel",
                "run-compete",
                "run-interrupt",
                "run-second",
                "parallel-cancel",
                "parallel-success",
                "parallel-failed",
                "parallel-repaired",
                "parallel-stale-recovered",
            )
        ],
    )
    issues: list[str] = []

    _invoke_restart_journey(tmp_path, issues, parallel=True)

    assert issues == []
    assert calls == ["access", "persist", "cancel", "repair", "stale", "primary"]
    assert running.stop_calls == 1


def test_crash_restart_journey_records_restart_failure_and_still_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    running = _Running("token-2")
    monkeypatch.setattr(acceptance, "_launch_dev", lambda root, env: running)
    monkeypatch.setattr(
        acceptance,
        "_exercise_access_boundaries",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("restart failure")),
    )
    issues: list[str] = []

    _invoke_restart_journey(tmp_path, issues, parallel=False)

    assert issues == ["restart failure"]
    assert running.stop_calls == 1


def test_accept_dev_runtime_simulated_full_journey_preserves_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vite = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")
    running = _Running("token")
    events: list[str] = []

    monkeypatch.setattr(acceptance, "_port_is_free", lambda port: True)
    monkeypatch.setattr(
        dev_runtime_repositories,
        "prepare_acceptance_repository",
        lambda path: events.append(f"prepare:{path.name}"),
    )
    monkeypatch.setattr(
        dev_runtime_repositories,
        "prepare_interruptible_acceptance_repository",
        lambda path, **_kwargs: events.append(f"interruptible:{path.name}"),
    )
    monkeypatch.setattr(
        acceptance,
        "prepare_local_runtime_environment",
        lambda *, temp, state, config: (
            {"OH_GOON_EXECUTORS_FILE": "/tmp/executors.toml"},
            False,
            None,
        ),
    )
    monkeypatch.setattr(acceptance, "_launch_dev", lambda root, env: running)
    monkeypatch.setattr(
        acceptance,
        "_exercise_access_boundaries",
        lambda **kwargs: events.append("access"),
    )
    run_ids = iter(["run-first", "run-compete"])
    monkeypatch.setattr(
        acceptance, "run_acceptance_goon", lambda **kwargs: next(run_ids)
    )
    monkeypatch.setattr(
        acceptance,
        "exercise_location_lifecycle",
        lambda **kwargs: events.append("location"),
    )
    monkeypatch.setattr(
        acceptance,
        "exercise_shared_plan",
        lambda *args, **kwargs: events.append("shared"),
    )
    starts = iter(["run-cancel", "run-interrupt"])
    monkeypatch.setattr(
        acceptance, "start_acceptance_goon", lambda **kwargs: next(starts)
    )
    pids = iter([111, 222])
    monkeypatch.setattr(
        acceptance, "wait_for_interruptible_step", lambda **kwargs: next(pids)
    )
    monkeypatch.setattr(
        acceptance,
        "team_run_request_payload",
        lambda base_url, repository, token, run_id: {
            "repository": repository,
            "fleet_run_id": run_id,
            "expected_pipeline_sha256": "a" * 64,
            "expected_repository_instance_id": "instance-current",
            "inputs": {},
        },
    )
    monkeypatch.setattr(
        acceptance,
        "_post_json_expect_error",
        lambda *args, **kwargs: {"code": kwargs["expected_code"]},
    )
    monkeypatch.setattr(acceptance, "_pid_is_alive", lambda pid: True)
    cancels = iter([{"status": "CANCELLING"}, {"status": "CANCELLED"}])
    monkeypatch.setattr(
        acceptance, "cancel_acceptance_goon", lambda **kwargs: next(cancels)
    )
    monkeypatch.setattr(
        acceptance,
        "wait_for_acceptance_run_status",
        lambda **kwargs: {"outcome": "CANCELLED"},
    )
    monkeypatch.setattr(acceptance, "_wait_pid_dead", lambda pid: True)
    monkeypatch.setattr(
        acceptance, "_request_json", lambda *args, **kwargs: {"active_fleet_run": None}
    )
    monkeypatch.setattr(acceptance, "_wait_ports_free", lambda: True)
    monkeypatch.setattr(
        acceptance,
        "_verify_crash_restart_journey",
        lambda **kwargs: events.append("restart"),
    )

    issues = acceptance.accept_dev_runtime(tmp_path)

    assert issues == ()
    assert "access" in events
    assert "location" in events
    assert "shared" in events
    assert running.crash_calls == 1
    assert "restart" in events
    assert running.stop_calls == 0


def test_accept_dev_runtime_stops_gracefully_when_pre_restart_journey_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vite = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")
    running = _Running("token")

    monkeypatch.setattr(acceptance, "_port_is_free", lambda port: True)
    monkeypatch.setattr(
        dev_runtime_repositories, "prepare_acceptance_repository", lambda path: None
    )
    monkeypatch.setattr(
        dev_runtime_repositories,
        "prepare_interruptible_acceptance_repository",
        lambda path, **_kwargs: None,
    )
    monkeypatch.setattr(
        acceptance,
        "prepare_local_runtime_environment",
        lambda *, temp, state, config: (
            {"OH_GOON_EXECUTORS_FILE": "/tmp/executors.toml"},
            False,
            None,
        ),
    )
    monkeypatch.setattr(acceptance, "_launch_dev", lambda root, env: running)
    monkeypatch.setattr(
        acceptance,
        "_exercise_access_boundaries",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("access failed")),
    )
    monkeypatch.setattr(acceptance, "_wait_ports_free", lambda: True)

    assert acceptance.accept_dev_runtime(tmp_path) == ("access failed",)
    assert running.stop_calls == 1
