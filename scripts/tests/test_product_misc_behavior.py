from __future__ import annotations

from typing import Self, cast
from collections.abc import Sequence

import subprocess
import time
import urllib.request

import json
import os
from pathlib import Path
from collections.abc import Iterator
import socket
from types import SimpleNamespace
import urllib.error

import pytest

from oh_goon.integrations import fleet_plan_diagnostics as diagnostics
from oh_goon.repository_tooling.acceptance import product
from oh_goon.repository_tooling import process_metrics
from oh_goon.repository_tooling.runtime_loopback_guard import (
    LoopbackNetworkGuard,
    _allowed_address,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template


class Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_product_api_and_runtime_delegate_exact_journey(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prepared = []
    monkeypatch.setattr(
        product, "prepare_acceptance_repository", lambda path: prepared.append(path)
    )
    monkeypatch.setattr(product, "run_acceptance_goon", lambda **kwargs: "run-1")
    browsers = []
    monkeypatch.setattr(
        product,
        "run_runtime_browser_acceptance",
        lambda **kwargs: browsers.append(kwargs),
    )
    repo = tmp_path / "repo"
    assert (
        product.exercise_product_api(
            base_url="http://x",
            acceptance_repository=repo,
            server_repository_path="/repo",
            access_token="t",
        )
        == "run-1"
    )
    assert prepared == [repo]
    assert (
        product.exercise_product_runtime(
            root=tmp_path,
            base_url="http://x",
            acceptance_repository=repo,
            server_repository_path="/repo",
            browser_repository_path="/repos",
            access_token="t",
        )
        == "run-1"
    )
    assert browsers and browsers[-1]["run_id"] == "run-1"


def test_get_json_and_wait_http_success_retry_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_a, **_k: Response({"ok": True})
    )
    assert product._get_json("http://x", access_token="t") == {"ok": True}
    replies: Iterator[urllib.error.URLError | Response] = iter(
        [urllib.error.URLError("down"), Response({}, status=204)]
    )

    def open_retry(*_a: object, **_k: object) -> Response:
        item = next(replies)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(urllib.request, "urlopen", open_retry)
    monkeypatch.setattr(time, "sleep", lambda _x: None)
    ticks = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 0.2))
    product._wait_http("http://x", timeout_seconds=1)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    ticks = iter([0.0, 0.1, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 2.0))
    with pytest.raises(RuntimeError, match="timed out waiting"):
        product._wait_http("http://x", timeout_seconds=1)


def test_location_lifecycle_preserves_history_and_restores_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    configured = str(root.resolve())
    catalog = [{"path": str(root / "repo-a")}, {"path": str(root / "repo-b")}]
    replies = iter(
        [
            {"locations": [configured], "unavailable": []},
            catalog,
            {"locations": [configured], "unavailable": [configured]},
            [],
            {"game_tape": ["acceptance-step"]},
            {"locations": [configured], "unavailable": []},
            catalog,
        ]
    )
    monkeypatch.setattr(product, "_get_json", lambda *_a, **_k: next(replies))
    product.exercise_location_lifecycle(
        base_url="x", configured_root=root, run_id="run", access_token="t"
    )
    assert (
        root.is_dir() and not root.with_name("repos-temporarily-unavailable").exists()
    )


def test_location_lifecycle_rejects_missing_location_and_existing_move(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    monkeypatch.setattr(product, "_get_json", lambda *_a, **_k: {"locations": []})
    with pytest.raises(RuntimeError, match="not persisted"):
        product.exercise_location_lifecycle(
            base_url="x", configured_root=root, run_id="r", access_token="t"
        )
    moved = root.with_name("repos-temporarily-unavailable")
    moved.mkdir()
    monkeypatch.setattr(
        product, "_get_json", lambda *_a, **_k: {"locations": [str(root.resolve())]}
    )
    with pytest.raises(RuntimeError, match="already exists"):
        product.exercise_location_lifecycle(
            base_url="x", configured_root=root, run_id="r", access_token="t"
        )


def test_location_lifecycle_restores_even_when_unavailable_evidence_is_bad(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    configured = str(root.resolve())
    replies = iter(
        [
            {"locations": [configured]},
            [{"path": str(root / "repo")}],
            {"unavailable": []},
        ]
    )
    monkeypatch.setattr(product, "_get_json", lambda *_a, **_k: next(replies))
    with pytest.raises(RuntimeError, match="not reported as unavailable"):
        product.exercise_location_lifecycle(
            base_url="x", configured_root=root, run_id="r", access_token="t"
        )
    assert root.exists()


def test_stop_process_graceful_and_kill_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    graceful = SimpleNamespace(
        terminate=lambda: None, wait=lambda timeout: 0, kill=lambda: pytest.fail("kill")
    )
    product._stop(cast(subprocess.Popen[bytes], graceful))
    calls: list[str | tuple[str, float]] = []

    class Stuck:
        def terminate(self) -> None:
            calls.append("terminate")

        def wait(self, timeout: float) -> int:
            calls.append(("wait", timeout))
            if timeout == 10:
                raise subprocess.TimeoutExpired("x", timeout)
            return 0

        def kill(self) -> None:
            calls.append("kill")

    product._stop(cast(subprocess.Popen[bytes], Stuck()))
    assert calls == ["terminate", ("wait", 10), "kill", ("wait", 5)]


def test_source_runtime_environment_strips_ambient_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OH_GOON_SECRET", "x")
    monkeypatch.setenv("PYTHONPATH", "/ambient")
    monkeypatch.setenv("VIRTUAL_ENV", "/venv")
    monkeypatch.setenv("UV_RESOLUTION", "lowest")
    monkeypatch.setenv("UV_PRERELEASE", "always")
    monkeypatch.setenv("UV_EXCLUDE_NEWER", "2020-01-01")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/ambient/project-env")
    env = product._source_runtime_environment(tmp_path, tmp_path / "state")
    for name in (
        "OH_GOON_SECRET",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_RESOLUTION",
        "UV_PRERELEASE",
        "UV_EXCLUDE_NEWER",
        "UV_PROJECT_ENVIRONMENT",
    ):
        assert name not in env
    assert env["OH_GOON_SCOUT_ENABLED"] == "1" and env["OH_GOON_DB"].endswith(
        "oh-goon.db"
    )


def _fake_process() -> SimpleNamespace:
    return SimpleNamespace(
        terminate=lambda: None, wait=lambda timeout: 0, kill=lambda: None
    )


def test_accept_source_product_backend_only_and_browser_dependency_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(product, "_free_port", lambda: 1234)
    monkeypatch.setattr(product, "new_certification_access_token", lambda: "token")
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _fake_process())
    monkeypatch.setattr(product, "_wait_http", lambda *_a, **_k: None)
    monkeypatch.setattr(product, "exercise_product_api", lambda **_k: "run")
    monkeypatch.setattr(product, "exercise_location_lifecycle", lambda **_k: None)
    monkeypatch.setattr(product, "exercise_shared_plan", lambda *_a, **_k: None)
    stopped = []
    monkeypatch.setattr(product, "_stop", lambda proc: stopped.append(proc))
    assert product.accept_source_product(tmp_path, include_browser=False) == ()
    assert stopped
    browser = []
    monkeypatch.setattr(
        product,
        "run_runtime_browser_acceptance",
        lambda **kwargs: browser.append(kwargs),
    )
    assert product.accept_source_product(tmp_path, include_browser=True) == ()
    assert browser and browser[0]["run_id"] == "run"


def test_product_main_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(product, "accept_source_product", lambda *_a, **_k: ())
    assert product.main(["--backend-only"]) == 0
    assert "Game Tape" in capsys.readouterr().out
    monkeypatch.setattr(product, "accept_source_product", lambda *_a, **_k: ("broken",))
    assert product.main([]) == 1
    assert "broken" in capsys.readouterr().err
    monkeypatch.setattr(
        product,
        "accept_source_product",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert product.main([]) == 1
    assert "boom" in capsys.readouterr().err


def test_loopback_address_policy() -> None:
    for value in (
        "/tmp/socket",
        b"socket",
        ("localhost", 1),
        ("127.0.0.1", 1),
        ("::1", 1),
    ):
        assert _allowed_address(value) is True
    for denied in ((), (123, 1), ("example.com", 443), ("8.8.8.8", 53), object()):
        assert _allowed_address(denied) is False


def test_loopback_guard_records_blocked_connect_and_restores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = LoopbackNetworkGuard(tmp_path / "audit.jsonl")
    original_connect = socket.socket.connect
    guard.install()
    guard.install()
    assert socket.socket.connect is not original_connect
    sock = socket.socket()
    try:
        with pytest.raises(OSError, match="blocked non-loopback"):
            socket.socket.connect(sock, ("8.8.8.8", 53))
        assert socket.socket.connect_ex(sock, ("8.8.8.8", 53)) == 101
    finally:
        sock.close()
        guard.uninstall()
        guard.uninstall()
    assert socket.socket.connect is original_connect
    rows = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert {row["operation"] for row in rows} == {"connect", "connect_ex"}


def test_process_metrics_real_process_and_invalid_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sample = process_metrics.read_linux_process_sample(os.getpid())
    assert sample.cpu_seconds >= 0 and sample.rss_bytes > 0
    proc = tmp_path / "123"
    proc.mkdir()
    (proc / "stat").write_text("invalid", encoding="utf-8")
    (proc / "status").write_text("", encoding="utf-8")
    monkeypatch.setattr(process_metrics, "Path", lambda _root: tmp_path)
    with pytest.raises(RuntimeError, match="invalid /proc"):
        process_metrics.read_linux_process_sample(123)
    (proc / "stat").write_text("123 (x) R 1 2\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="incomplete /proc"):
        process_metrics.read_linux_process_sample(123)


def test_process_metrics_rejects_unknown_memory_unit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc = tmp_path / "5"
    proc.mkdir()
    fields = ["0"] * 13
    (proc / "stat").write_text("5 (x) R " + " ".join(fields) + "\n", encoding="utf-8")
    (proc / "status").write_text("VmRSS: 10 MB\n", encoding="utf-8")
    monkeypatch.setattr(process_metrics, "Path", lambda _root: tmp_path)
    with pytest.raises(RuntimeError, match=r"unexpected VmRSS: unit"):
        process_metrics.read_linux_process_sample(5)


def test_plan_diagnostics_files_root_and_flatten(tmp_path: Path) -> None:
    one = tmp_path / "one.yml"
    one.write_text("version: 1\n")
    two = tmp_path / "two.yaml"
    two.write_text("version: 1\n")
    assert diagnostics._plan_files(one) == (one,)
    assert set(diagnostics._plan_files(tmp_path)) == {one, two}
    with pytest.raises(FleetPlanInvalid, match="does not exist"):
        diagnostics._plan_files(tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FleetPlanInvalid, match="contains no YAML"):
        diagnostics._plan_files(empty)
    assert diagnostics._flatten({"a": {"b": 1}, "c": 2}) == {"a.b": 1, "c": 2}
    assert diagnostics._flatten("x", "p") == {"p": "x"}
    changes = diagnostics._field_changes({"a": 1}, {"a": 2, "b": 3})
    assert [item.field for item in changes] == ["a", "b"]


def test_plan_diagnostic_root_containment(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    root.mkdir()
    plan = root / "a.yml"
    plan.write_text("version: 1\n")
    assert diagnostics._diagnostic_root(root, None) == root
    assert diagnostics._diagnostic_root(plan, None) == root
    assert diagnostics._diagnostic_root(plan, root) == root
    with pytest.raises(FleetPlanInvalid, match="not a directory"):
        diagnostics._diagnostic_root(plan, tmp_path / "missing")
    outside = tmp_path.parent / "outside.yml"
    outside.write_text("x")
    try:
        with pytest.raises(FleetPlanInvalid):
            diagnostics._diagnostic_root(outside, root)
    finally:
        outside.unlink(missing_ok=True)


def _plan(*, steps: Sequence[ResolvedCommandStep], name: str) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name=name,
        description="d",
        execution=VerificationExecutionPolicy(),
        steps=tuple(steps),
    )


def _step(step_id: str, *, run: str) -> ResolvedCommandStep:
    return ResolvedCommandStep(step_id=step_id, argv=(run,))


def test_semantic_plan_diff_added_removed_changed_and_plan_fields() -> None:
    before = _plan(name="old", steps=[_step("same", run="a"), _step("gone", run="x")])
    after = _plan(name="new", steps=[_step("same", run="b"), _step("added", run="y")])
    diff = diagnostics.semantic_plan_diff(before, after)
    assert diff.before_plan_id == before.plan_id and diff.after_plan_id == after.plan_id
    assert any(item.field == "name" for item in diff.plan_fields)
    kinds = {item.step_id: item.kind.value for item in diff.steps}
    assert kinds == {"added": "added", "gone": "removed", "same": "changed"}


def test_check_plan_sources_override_and_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = tmp_path / "a.yml"
    plan.write_text("bad", encoding="utf-8")
    compiled = parse_goon_template(
        "version: 1\nsteps: [{run: echo}]\n", source="fixture.yml"
    )
    report = diagnostics.check_plan_sources(plan, compiled_overrides={plan: compiled})
    assert report.checked_files == 1 and report.issues == ()
    monkeypatch.setattr(
        diagnostics,
        "load_plan_template",
        lambda _path: (_ for _ in ()).throw(FleetPlanInvalid("bad plan")),
    )
    report = diagnostics.check_plan_sources(plan)
    assert len(report.issues) == 1 and "bad plan" in report.issues[0].message
