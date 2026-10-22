from __future__ import annotations

import os
import subprocess
from pathlib import Path

import oh_goon.services.certification_frontdoor as frontdoor


def test_certification_frontdoor_does_not_run_developer_setup_before_workflow(
    monkeypatch,
) -> None:
    observed: list[list[str]] = []
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "/tmp/executors.json")
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main",
        lambda args: observed.append(list(args)) or 0,
    )
    monkeypatch.setattr(
        frontdoor,
        "run_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("certification frontdoor must not invoke make setup")
        ),
    )

    assert frontdoor.main(["app"]) == 0
    assert observed == [["app"]]


def test_certification_frontdoor_has_no_local_dependency_materialization_owner() -> None:
    assert not hasattr(frontdoor, "_run_local_dependency_preflight")


def test_app_verification_does_not_require_strict_executor_delegation(monkeypatch) -> None:
    observed: list[list[str]] = []
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.setattr(
        frontdoor,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(
            AssertionError("APP verification must not resolve strict cgroup authority")
        ),
    )
    monkeypatch.setattr(
        frontdoor,
        "_enter_systemd_delegation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("APP verification must not enter systemd delegation")
        ),
    )
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main_with_state_lease",
        lambda args: observed.append(list(args)) or 0,
    )

    assert frontdoor.main([]) == 0
    assert observed == [[]]


def test_release_keeps_strict_executor_boundary(monkeypatch) -> None:
    observed: list[tuple[list[str], str]] = []
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    monkeypatch.delenv("OH_GOON_CERTIFICATION_CGROUP_ROOT", raising=False)

    def unavailable(_environment):
        raise frontdoor.FleetPlanInvalid("no delegated cgroup")

    monkeypatch.setattr(frontdoor, "resolve_certification_cgroup_root", unavailable)
    monkeypatch.setattr(
        frontdoor,
        "_enter_systemd_delegation",
        lambda args, *, current_reason: observed.append((list(args), current_reason)) or 17,
    )

    assert frontdoor.main(["release"]) == 17
    assert observed == [(["release"], "no delegated cgroup")]


def test_delegated_reentry_does_not_repeat_static_admission(monkeypatch) -> None:
    observed: list[list[str]] = []
    monkeypatch.setattr(
        frontdoor,
        "_static_source_admission",
        lambda: (_ for _ in ()).throw(
            AssertionError("delegated re-entry must consume the parent's static admission")
        ),
    )
    monkeypatch.setattr(
        frontdoor,
        "_run_delegated_reentry",
        lambda args: observed.append(list(args)) or 0,
    )

    assert frontdoor.main([frontdoor._REENTRY_FLAG, "env", "ready", "release"]) == 0
    assert observed == [["env", "ready", "release"]]


def test_delegated_reentry_ignores_repository_python_imports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "untrusted-import"
    poison = (
        f"open({str(marker)!r}, 'w').write('injected')\n"
        "raise RuntimeError('untrusted repository import')\n"
    )
    package = tmp_path / "oh_goon"
    package.mkdir()
    (package / "__init__.py").write_text(poison, encoding="utf-8")
    (tmp_path / "sitecustomize.py").write_text(poison, encoding="utf-8")
    command = frontdoor._systemd_reentry_command(
        systemd_run=Path("/usr/bin/systemd-run"),
        env_file=tmp_path / "missing-environment.json",
        ready_file=tmp_path / "ready",
        workflow_args=["release"],
    )

    # Exercise the exact Python re-entry while stopping before environment
    # restoration, systemd delegation, or any certification workload.
    result = subprocess.run(
        command[command.index("--") + 1 :],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 2, result.stderr
    assert "cannot restore certification environment" in result.stdout
    assert "No certification Goon ran." in result.stdout
    assert not marker.exists()
    assert not (tmp_path / "ready").exists()
