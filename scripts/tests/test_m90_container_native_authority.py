from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release import accept_container as subject


def _container_source(root: Path) -> None:
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
