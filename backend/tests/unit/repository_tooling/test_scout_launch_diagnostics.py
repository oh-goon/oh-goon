from __future__ import annotations

import ast
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from oh_goon.repository_tooling import scout_http_load_probe


class _ExitedProcess:
    def __init__(self, *, returncode: int, stderr: bytes) -> None:
        self._returncode = returncode
        self.stderr = BytesIO(stderr)

    def poll(self) -> int:
        return self._returncode


class _LiveProcess:
    stderr = None

    def poll(self) -> None:
        return None


def test_wait_json_fails_immediately_with_child_exit_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def unexpected_get(url: str) -> dict[str, Any]:
        requested.append(url)
        raise AssertionError("HTTP must not be attempted after the child has exited")

    monkeypatch.setattr(scout_http_load_probe, "_get_json", unexpected_get)
    process = _ExitedProcess(
        returncode=17,
        stderr=b"dependency launch failed\nmissing retained supply\n",
    )
    url = "http://127.0.0.1:43123/api/health"

    with pytest.raises(RuntimeError) as captured:
        scout_http_load_probe._wait_json(  # type: ignore[arg-type]
            url,
            timeout_seconds=30.0,
            process=process,
        )

    message = str(captured.value)
    assert "exited with code 17" in message
    assert url in message
    assert "dependency launch failed" in message
    assert "missing retained supply" in message
    assert requested == []


def test_wait_json_preserves_healthy_readiness_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"status": "ok"}
    monkeypatch.setattr(scout_http_load_probe, "_get_json", lambda _url: expected)

    assert (
        scout_http_load_probe._wait_json(  # type: ignore[arg-type]
            "http://127.0.0.1:43124/api/health",
            process=_LiveProcess(),
        )
        == expected
    )


def test_wait_json_bounds_reported_stderr() -> None:
    process = _ExitedProcess(
        returncode=23,
        stderr=(b"discard-me\n" * 2000) + b"authoritative-tail\n",
    )

    with pytest.raises(RuntimeError) as captured:
        scout_http_load_probe._wait_json(  # type: ignore[arg-type]
            "http://127.0.0.1:43125/api/health",
            process=process,
        )

    message = str(captured.value)
    assert "authoritative-tail" in message
    assert len(message) < 13000


def test_all_scout_readiness_waits_bind_a_process() -> None:
    tooling = Path(scout_http_load_probe.__file__).resolve().parent
    owners = (
        tooling / "scout_http_load_probe.py",
        tooling / "scout_corruption_recovery_certification.py",
        tooling / "scout_crash_restart_certification.py",
        tooling / "scout_failure_isolation_certification.py",
        tooling / "scout_retention_checkpoint_certification.py",
        tooling / "scout_reverse_proxy_certification.py",
        tooling / "scout_runtime_overhead_certification.py",
    )

    calls = 0
    for owner in owners:
        tree = ast.parse(owner.read_text(encoding="utf-8"), filename=str(owner))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "_wait_json":
                continue
            calls += 1
            process_keywords = [kw for kw in node.keywords if kw.arg == "process"]
            assert len(process_keywords) == 1, f"{owner} has an unbound _wait_json call"

    assert calls == 14


def test_reverse_proxy_readiness_binds_backend_and_proxy_processes() -> None:
    tooling = Path(scout_http_load_probe.__file__).resolve().parent
    source = (tooling / "scout_reverse_proxy_certification.py").read_text(encoding="utf-8")

    assert source.count("process=backend") == 2
    assert source.count("process=proxy") == 2


def test_scout_server_launch_rejects_ambient_python_and_uv_project_authority(
    monkeypatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}
    for name, value in {
        "PYTHONHOME": "/ambient/home",
        "PYTHONPATH": "/ambient/src",
        "VIRTUAL_ENV": "/ambient/venv",
        "UV_PROJECT_ENVIRONMENT": "/ambient/project-env",
        "UV_RESOLUTION": "lowest",
        "UV_PRERELEASE": "always",
        "UV_EXCLUDE_NEWER": "2020-01-01",
    }.items():
        monkeypatch.setenv(name, value)

    def fake_popen(argv, **kwargs):
        observed["argv"] = argv
        observed["env"] = kwargs["env"]
        return object()

    monkeypatch.setattr(scout_http_load_probe.subprocess, "Popen", fake_popen)
    scout_http_load_probe._start_server(
        root=tmp_path,
        state=tmp_path / "state.db",
        league=tmp_path / "league.yml",
        port=8123,
    )
    env = observed["env"]
    assert isinstance(env, dict)
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "UV_RESOLUTION",
        "UV_PRERELEASE",
        "UV_EXCLUDE_NEWER",
    ):
        assert name not in env
    assert observed["argv"][:6] == ("uv", "run", "--frozen", "--no-dev", "--project", "backend")
