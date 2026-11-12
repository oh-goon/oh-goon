from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from oh_goon.repository_tooling.release import accept_container as subject


def _container_source(root: Path) -> None:
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    (root / "docker/ubuntu").mkdir(parents=True)
    (root / "backend/pyproject.toml").write_text(
        "[project]\nname='x'\n", encoding="utf-8"
    )
    (root / "backend/uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "frontend/package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    (root / "frontend/package-lock.json").write_text(
        '{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8"
    )
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "docker/ubuntu/Dockerfile").write_text("FROM scratch\n", encoding="utf-8")


def test_container_acceptance_does_not_consult_host_dependency_lock_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _container_source(tmp_path)
    # Exact M89 imported this hook and called it before Compose. If that old
    # authority is present, make the regression fail at the reproduced boundary.
    if hasattr(subject, "dependency_lock_issues"):
        monkeypatch.setattr(
            subject,
            "dependency_lock_issues",
            lambda _root: (_ for _ in ()).throw(
                AssertionError("host dependency-lock qualification must not run")
            ),
        )

    calls: list[tuple[str, ...]] = []

    def native_run(
        command: tuple[str, ...], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        raise RuntimeError("compose build reached")

    monkeypatch.setattr(subprocess, "run", native_run)
    with pytest.raises(RuntimeError, match="compose build reached"):
        subject.accept_container(tmp_path)

    assert calls
    assert calls[0][:2] == ("docker", "compose")
    assert calls[0][-2:] == ("build", "oh-goon")
    assert all(command[0] not in {"uv", "npm"} for command in calls)


def test_container_acceptance_preserves_repository_privacy_before_compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _container_source(tmp_path)
    (tmp_path / "frontend/package-lock.json").write_text(
        '{"lockfileVersion":3,"packages":{"node_modules/x":{"resolved":"https://registry.internal/x.tgz"}}}\n',
        encoding="utf-8",
    )

    def forbidden_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("privacy rejection must happen before Compose")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    issues = subject.accept_container(tmp_path)
    assert issues
    assert any("private infrastructure" in issue for issue in issues)
    assert all("registry.internal" not in issue for issue in issues)


def test_container_acceptance_preserves_native_compose_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _container_source(tmp_path)
    if hasattr(subject, "dependency_lock_issues"):
        monkeypatch.setattr(subject, "dependency_lock_issues", lambda _root: ())

    def native_run(command: tuple[str, ...], **_kwargs: object) -> object:
        if tuple(command)[-2:] == ("build", "oh-goon"):
            raise subprocess.CalledProcessError(17, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", native_run)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        subject.accept_container(tmp_path)
    assert exc.value.returncode == 17


@pytest.fixture
def container_journey(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Keep the original acceptance flow, with controlled native effects."""
    state: dict[str, Any] = {
        "directory": None,
        "commands": [],
        "failure": None,
        "cleanup_failure": None,
        "cleanup_status": 0,
        "provenance_invalid": False,
        "requests": [],
        "product_runs": [],
    }
    make_directory = tempfile.mkdtemp
    remove_directory = shutil.rmtree

    def allocate(*args: Any, **kwargs: Any) -> str:
        directory: str = make_directory(*args, **kwargs)
        state["directory"] = Path(directory)
        return directory

    def native_run(
        command: tuple[str, ...], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        state["commands"].append(tuple(command))
        status = 0
        if command[:2] == ("docker", "compose"):
            data = Path(kwargs["env"]["OH_GOON_DATA_DIR"])
            if command[-1] == "oh-goon-runtime-identity":
                origin = (
                    "/unexpected/oh_goon/__init__.py"
                    if state["provenance_invalid"]
                    else "/venv/lib/site-packages/oh_goon/__init__.py"
                )
                return subprocess.CompletedProcess(
                    command, 0, stdout=f"{origin}\n1.2.3\n"
                )
            if command[-2:] == ("build", "oh-goon"):
                (data / "oh-goon.db").write_bytes(b"synthetic persisted state")
                if state["failure"] is not None:
                    raise state["failure"]
            if "--remove-orphans" in command:
                if state["cleanup_failure"] is not None:
                    raise state["cleanup_failure"]
                status = state["cleanup_status"]
        if kwargs.get("check") and status:
            raise subprocess.CalledProcessError(status, command)
        return subprocess.CompletedProcess(command, status)

    class Response(io.BytesIO):
        status = 200

    def request(value: Any, **_kwargs: object) -> Response:
        method = "GET" if isinstance(value, str) else value.get_method()
        url = value if isinstance(value, str) else value.full_url
        state["requests"].append((method, url))
        if url.endswith("/healthz"):
            payload: dict[str, object] = {"healthy": True}
        elif url.endswith("/api/locations"):
            payload = {"locations": ["/repositories"]}
        elif url.endswith("/api/fleet/runs/fixture-run"):
            payload = {"fleet_run_id": "fixture-run"}
        else:
            raise AssertionError(f"unexpected acceptance HTTP request: {method} {url}")
        return Response(json.dumps(payload).encode())

    def product(**kwargs: object) -> str:
        state["product_runs"].append(kwargs)
        return "fixture-run"

    monkeypatch.setattr(tempfile, "mkdtemp", allocate)
    monkeypatch.setattr(subprocess, "run", native_run)
    monkeypatch.setattr(urllib.request, "urlopen", request)
    monkeypatch.setattr(
        subject, "new_certification_access_token", lambda: "fixture-access"
    )
    monkeypatch.setattr(subject, "exercise_product_runtime", product)
    try:
        yield state
    finally:
        # All processes in this fixture are inert. Test-owned retained state may
        # be removed after its assertions, including a simulated rmtree failure.
        directory = state["directory"]
        if directory is not None and directory.exists():
            remove_directory(directory)


def test_container_acceptance_removes_state_after_native_teardown_success(
    tmp_path: Path, container_journey: dict[str, Any]
) -> None:
    _container_source(tmp_path)
    assert subject.accept_container(tmp_path) == ()
    assert not container_journey["directory"].exists()
    assert sum("down" in call for call in container_journey["commands"]) == 2
    assert (
        sum(
            call[-3:] == ("up", "-d", "oh-goon")
            for call in container_journey["commands"]
        )
        == 2
    )
    assert len(container_journey["product_runs"]) == 1
    assert (
        "npm",
        "ci",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ) in container_journey["commands"]
    assert ("npm", "run", "browser:install") in container_journey["commands"]
    assert (
        sum(url.endswith("/healthz") for _method, url in container_journey["requests"])
        == 2
    )
    assert [
        method
        for method, url in container_journey["requests"]
        if url.endswith("/api/locations")
    ] == ["POST", "GET"]
    assert any(
        url.endswith("/api/fleet/runs/fixture-run")
        for _method, url in container_journey["requests"]
    )


@pytest.mark.parametrize("invalid_origin", [False, True])
def test_container_acceptance_retains_state_and_primary_issues_when_down_fails(
    tmp_path: Path, container_journey: dict[str, Any], invalid_origin: bool
) -> None:
    _container_source(tmp_path)
    container_journey["provenance_invalid"] = invalid_origin
    primary = subject.accept_container(tmp_path)
    assert bool(primary) == invalid_origin
    container_journey["cleanup_status"] = 21
    issues = subject.accept_container(tmp_path)
    directory = container_journey["directory"]
    assert directory.is_dir()
    assert (directory / "data/oh-goon.db").read_bytes() == b"synthetic persisted state"
    assert issues[: len(primary)] == primary
    assert any(
        "cleanup" in issue.lower() and str(directory) in issue for issue in issues
    )
    assert any("21" in issue for issue in issues)


@pytest.mark.parametrize(
    "primary",
    [
        subprocess.CalledProcessError(17, ("docker", "compose", "build")),
        RuntimeError("primary runtime failure"),
        KeyboardInterrupt(),
        SystemExit(19),
    ],
)
def test_container_acceptance_preserves_primary_exception_through_failed_teardown(
    tmp_path: Path, container_journey: dict[str, Any], primary: BaseException
) -> None:
    _container_source(tmp_path)
    container_journey["failure"] = primary
    container_journey["cleanup_status"] = 21
    with pytest.raises(type(primary)) as caught:
        subject.accept_container(tmp_path)
    assert caught.value is primary
    directory = container_journey["directory"]
    assert directory.is_dir()
    assert any(
        str(directory) in note and "cleanup" in note.lower()
        for note in primary.__notes__
    )


@pytest.mark.parametrize(
    "failure",
    [
        OSError("Docker unavailable"),
        subprocess.TimeoutExpired(("docker", "compose"), 60),
    ],
)
def test_container_acceptance_retains_state_when_teardown_cannot_complete(
    tmp_path: Path, container_journey: dict[str, Any], failure: Exception
) -> None:
    _container_source(tmp_path)
    container_journey["cleanup_failure"] = failure
    issues = subject.accept_container(tmp_path)
    assert container_journey["directory"].is_dir()
    assert any("cleanup" in issue.lower() for issue in issues)
    assert any(str(failure) in issue for issue in issues)


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(23)])
def test_container_acceptance_propagates_teardown_cancellation_and_retains_state(
    tmp_path: Path, container_journey: dict[str, Any], failure: BaseException
) -> None:
    _container_source(tmp_path)
    container_journey["cleanup_failure"] = failure
    with pytest.raises(type(failure)) as caught:
        subject.accept_container(tmp_path)
    assert caught.value is failure
    assert container_journey["directory"].is_dir()
    assert any(
        str(container_journey["directory"]) in note for note in failure.__notes__
    )


@pytest.mark.parametrize("primary", [None, RuntimeError("primary runtime failure")])
@pytest.mark.parametrize(
    "cleanup", [OSError("directory removal refused"), KeyboardInterrupt()]
)
def test_container_acceptance_reports_filesystem_cleanup_without_masking_primary(
    tmp_path: Path,
    container_journey: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    primary: BaseException | None,
    cleanup: BaseException,
) -> None:
    _container_source(tmp_path)
    container_journey["failure"] = primary

    def refuse_removal(*_args: object, **_kwargs: object) -> None:
        raise cleanup

    monkeypatch.setattr(shutil, "rmtree", refuse_removal)
    if primary is None and isinstance(cleanup, Exception):
        issues = subject.accept_container(tmp_path)
        assert any("directory removal refused" in issue for issue in issues)
    else:
        expected = primary if primary is not None else cleanup
        with pytest.raises(type(expected)) as caught:
            subject.accept_container(tmp_path)
        assert caught.value is expected
        assert any("removal may be partial" in note for note in expected.__notes__)


def test_container_acceptance_closes_state_on_pre_launch_setup_failure(
    tmp_path: Path, container_journey: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _container_source(tmp_path)

    def fail_port() -> int:
        raise RuntimeError("port allocation failed")

    monkeypatch.setattr(subject, "_allocate_acceptance_port", fail_port)
    with pytest.raises(RuntimeError, match="port allocation failed"):
        subject.accept_container(tmp_path)
    assert not container_journey["directory"].exists()
    assert container_journey["commands"] == []


def test_container_acceptance_cli_reports_primary_and_cleanup_failure(
    tmp_path: Path,
    container_journey: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _container_source(tmp_path)
    container_journey["failure"] = RuntimeError("primary runtime failure")
    container_journey["cleanup_status"] = 21
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["container-acceptance"])
    assert subject.main() == 1
    error = capsys.readouterr().err
    assert "primary runtime failure" in error
    assert "cleanup" in error.lower()
    assert str(container_journey["directory"]) in error
