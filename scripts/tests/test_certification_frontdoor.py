from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services import certification_frontdoor
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV


_REAL_STATIC_SOURCE_ADMISSION = certification_frontdoor._static_source_admission


@pytest.fixture(autouse=True)
def _admit_static_source(monkeypatch):
    # certification_frontdoor.main() intentionally updates process environment for
    # its CLI lifetime. Keep that side effect from leaking into unrelated tests that
    # execute later in this same pytest process.
    monkeypatch.delenv(SQLITE_JOURNAL_MODE_ENV, raising=False)
    monkeypatch.setattr(certification_frontdoor, "_static_source_admission", lambda: True)



def test_app_frontdoor_strips_inherited_strict_executor_authority_before_orchestration(
    monkeypatch,
) -> None:
    inherited = {
        "OH_GOON_EXECUTORS_FILE": "/operator/executors.toml",
        "OH_GOON_CERTIFICATION_CGROUP_ROOT": "/sys/fs/cgroup/operator",
        "OH_GOON_CERTIFICATION_SYSTEMD_DELEGATED": "1",
        "OH_GOON_CERTIFICATION_SYSTEMD_SUBGROUP": "strict-work",
    }
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    observed: dict[str, str | None] = {}

    def run_app(args: list[str]) -> int:
        assert args == ["explain", "run-1"]
        for name in inherited:
            observed[name] = os.environ.get(name)
        return 17

    monkeypatch.setattr(certification_frontdoor, "_workflow_main_with_state_lease", run_app)
    monkeypatch.setattr(
        certification_frontdoor,
        "_enter_systemd_delegation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("APP must not delegate")),
    )
    monkeypatch.setattr(
        certification_frontdoor,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(AssertionError("APP must not inspect strict placement")),
    )

    assert certification_frontdoor.main(["explain", "run-1"]) == 17
    assert observed == {name: None for name in inherited}


def test_app_frontdoor_strips_strict_executor_authority_before_static_admission(
    monkeypatch,
) -> None:
    inherited = {
        "OH_GOON_EXECUTORS_FILE": "/operator/executors.toml",
        "OH_GOON_CERTIFICATION_CGROUP_ROOT": "/sys/fs/cgroup/operator",
        "OH_GOON_CERTIFICATION_SYSTEMD_DELEGATED": "1",
        "OH_GOON_CERTIFICATION_SYSTEMD_SUBGROUP": "strict-work",
    }
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)

    def admit() -> bool:
        assert all(os.environ.get(name) is None for name in inherited)
        return True

    monkeypatch.setattr(certification_frontdoor, "_static_source_admission", admit)
    monkeypatch.setattr(
        certification_frontdoor, "_workflow_main_with_state_lease", lambda _args: 19
    )

    assert certification_frontdoor.main(["explain", "run-1"]) == 19


def test_strict_release_process_environment_preserves_explicit_executor_authority() -> None:
    inherited = {
        "OH_GOON_EXECUTORS_FILE": "/operator/executors.toml",
        "OH_GOON_CERTIFICATION_CGROUP_ROOT": "/sys/fs/cgroup/operator",
        "OH_GOON_CERTIFICATION_SYSTEMD_DELEGATED": "1",
        "OH_GOON_CERTIFICATION_SYSTEMD_SUBGROUP": "strict-work",
    }

    result = certification_frontdoor._certification_process_environment(
        inherited, require_strict_executor=True
    )

    for name, value in inherited.items():
        assert result[name] == value


def test_missing_systemd_reports_actionable_executor_failure(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    monkeypatch.delenv("OH_GOON_CERTIFICATION_CGROUP_ROOT", raising=False)
    monkeypatch.setattr(
        certification_frontdoor,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(FleetPlanInvalid("init.scope is not delegated")),
    )
    monkeypatch.setattr(certification_frontdoor.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        certification_frontdoor,
        "_workflow_main",
        lambda _args: (_ for _ in ()).throw(AssertionError("workflow must not start")),
    )

    assert certification_frontdoor.main(["release"]) == 2
    output = capsys.readouterr().out
    assert "CERTIFICATION EXECUTOR UNAVAILABLE" in output
    assert "Strict release/CI execution requires a delegated cgroup-v2 executor." in output
    assert "init.scope is not delegated" in output
    assert "systemd-run is not available on PATH" in output
    assert "OH_GOON_EXECUTORS_FILE" in output
    assert "Delegate=yes" in output
    assert "No certification Goon ran." in output


def test_delegated_reentry_rejects_non_strict_app_purpose_before_restoring_authority(
    tmp_path, monkeypatch, capsys
) -> None:
    env_file = tmp_path / "environment.json"
    ready_file = tmp_path / "ready"
    env_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        certification_frontdoor,
        "_read_secure_environment",
        lambda _path: (_ for _ in ()).throw(AssertionError("APP re-entry must fail before environment restore")),
    )

    assert certification_frontdoor._run_delegated_reentry(
        [str(env_file), str(ready_file), "explain", "run-1"]
    ) == 2
    output = capsys.readouterr().out
    assert "explicit strict release/CI workflow" in output
    assert "No certification Goon ran." in output


def test_systemd_command_delegates_without_putting_environment_on_command_line(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "environment.json"
    ready_file = tmp_path / "ready"
    command = certification_frontdoor._systemd_reentry_command(
        systemd_run=Path("/usr/bin/systemd-run"),
        env_file=env_file,
        ready_file=ready_file,
        workflow_args=["release"],
    )
    joined = " ".join(command)

    assert command[:6] == [
        "/usr/bin/systemd-run",
        "--user",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
    ]
    assert "--property=Type=exec" in command
    assert "--property=Delegate=yes" in command
    assert not any(item.startswith("--property=DelegateSubgroup=") for item in command)
    assert f"--working-directory={tmp_path.resolve()}" in command
    assert sys.executable in command
    assert "oh_goon.services.certification_frontdoor" in command
    assert str(env_file) in command
    assert str(ready_file) in command
    assert "SECRET_VALUE" not in joined


def test_default_app_reentry_preserves_an_empty_workflow_argument_list(tmp_path, monkeypatch) -> None:
    command = certification_frontdoor._systemd_reentry_command(
        systemd_run=Path("/usr/bin/systemd-run"),
        env_file=tmp_path / "environment.json",
        ready_file=tmp_path / "ready",
        workflow_args=[],
    )
    index = command.index("--oh-goon-delegated-reentry")
    assert command[index + 1 :] == [str(tmp_path / "environment.json"), str(tmp_path / "ready")]
    assert "--" not in command[index + 1 :]


def test_reentry_environment_file_is_private_and_consumed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = certification_frontdoor._write_reentry_environment(
        {"PATH": "/usr/bin", "SECRET": "SECRET_VALUE", "XDG_RUNTIME_DIR": str(tmp_path)}
    )

    info = path.stat()
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == os.getuid()
    restored = certification_frontdoor._read_secure_environment(path)
    assert restored["SECRET"] == "SECRET_VALUE"
    assert not path.exists()


def test_systemd_failure_preserves_exact_reason_and_does_not_run_workflow(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    monkeypatch.delenv("OH_GOON_CERTIFICATION_CGROUP_ROOT", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        certification_frontdoor,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(FleetPlanInvalid("current scope cannot delegate")),
    )
    monkeypatch.setattr(certification_frontdoor.shutil, "which", lambda _name: "/usr/bin/systemd-run")
    monkeypatch.setattr(
        certification_frontdoor,
        "_stream",
        lambda _args: (1, "Failed to connect to bus: No medium found\n"),
    )
    monkeypatch.setattr(
        certification_frontdoor,
        "_workflow_main",
        lambda _args: (_ for _ in ()).throw(AssertionError("workflow must not start")),
    )

    assert certification_frontdoor.main(["release"]) == 1
    output = capsys.readouterr().out
    assert "CERTIFICATION EXECUTOR UNAVAILABLE" in output
    assert "Failed to connect to bus: No medium found" in output
    assert "No certification Goon ran." in output


def test_bus_failure_diagnoses_wsl_without_systemd_pid1(monkeypatch) -> None:
    def read_text(path: Path) -> str:
        if path == Path("/proc/sys/kernel/osrelease"):
            return "6.18.0-microsoft-standard-WSL2"
        if path == Path("/proc/version"):
            return "Linux version test Microsoft"
        if path == Path("/proc/1/comm"):
            return "init"
        raise AssertionError(path)

    monkeypatch.setattr(certification_frontdoor, "_read_text", read_text)
    monkeypatch.setattr(certification_frontdoor, "_wsl_systemd_setting", lambda: "false")
    monkeypatch.setattr(certification_frontdoor, "_socket_status", lambda _path: "missing")

    diagnosis = certification_frontdoor._systemd_failure_diagnosis(
        {"XDG_RUNTIME_DIR": "/run/user/1000"},
        "Failed to connect to bus: No such file or directory",
    )

    assert "environment: WSL detected" in diagnosis
    assert "PID 1: init" in diagnosis
    assert "/etc/wsl.conf [boot] systemd: false" in diagnosis
    assert "systemd user bus: missing (/run/user/1000/bus)" in diagnosis
    assert "wsl.exe --shutdown" in diagnosis
    assert "verify: systemctl status" in diagnosis
    assert "Rerun: make certify" in diagnosis


def test_bus_failure_diagnoses_missing_user_manager_when_systemd_is_pid1(
    monkeypatch,
) -> None:
    def read_text(path: Path) -> str:
        if path == Path("/proc/sys/kernel/osrelease"):
            return "6.8.0-generic"
        if path == Path("/proc/version"):
            return "Linux version test"
        if path == Path("/proc/1/comm"):
            return "systemd"
        raise AssertionError(path)

    monkeypatch.setattr(certification_frontdoor, "_read_text", read_text)

    diagnosis = certification_frontdoor._systemd_failure_diagnosis(
        {}, "Failed to connect to bus: No medium found"
    )

    assert "PID 1: systemd" in diagnosis
    assert "environment: WSL not detected" in diagnosis
    assert "XDG_RUNTIME_DIR: not set" in diagnosis
    assert "systemd user bus: cannot be derived" in diagnosis
    assert "systemd is PID 1" in diagnosis
    assert "systemctl --user status" in diagnosis
    assert "wsl.exe --shutdown" not in diagnosis


def test_systemd_child_readiness_means_child_exit_is_the_workflow_result(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    monkeypatch.delenv("OH_GOON_CERTIFICATION_CGROUP_ROOT", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        certification_frontdoor,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(FleetPlanInvalid("current scope cannot delegate")),
    )
    monkeypatch.setattr(certification_frontdoor.shutil, "which", lambda _name: "/usr/bin/systemd-run")

    def stream(command: list[str]) -> tuple[int, str]:
        ready = Path(command[command.index("--oh-goon-delegated-reentry") + 2])
        ready.write_text("/sys/fs/cgroup/user.slice/delegated.service\n", encoding="utf-8")
        return 23, "APPLICATION FAILED\n"

    monkeypatch.setattr(certification_frontdoor, "_stream", stream)

    assert certification_frontdoor.main(["release"]) == 23
    assert "CERTIFICATION EXECUTOR UNAVAILABLE" not in capsys.readouterr().out


def test_workflow_enters_certification_state_lease_before_orchestration(tmp_path, monkeypatch) -> None:
    from contextlib import contextmanager

    observed: list[object] = []
    state = tmp_path / "cert-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    monkeypatch.setattr(
        certification_frontdoor,
        "certification_state_root",
        lambda *, source_root, state_home: state,
    )

    @contextmanager
    def lease(path: Path):
        observed.append(("lease-enter", path))
        try:
            yield
        finally:
            observed.append(("lease-exit", path))

    monkeypatch.setattr(certification_frontdoor, "certification_state_lease", lease)
    monkeypatch.setattr(
        certification_frontdoor,
        "preflight_certification_database_under_lease",
        lambda *, source_root, state_home: observed.append(
            ("preflight", source_root, state_home)
        ) or {"database": str(state / "oh-goon.db")},
    )
    monkeypatch.setattr(
        certification_frontdoor,
        "reconcile_certification_run_workspaces",
        lambda *, state_home, owned_state_root: observed.append(
            ("reconcile", state_home, owned_state_root)
        ) or type("Result", (), {"removed": (), "refused": ()})(),
    )
    monkeypatch.setattr(
        certification_frontdoor,
        "_workflow_main",
        lambda args: observed.append(("workflow", tuple(args))) or 17,
    )

    assert certification_frontdoor._workflow_main_with_state_lease(["app"]) == 17
    assert observed[0] == ("lease-enter", state)
    assert observed[1][0] == "preflight"
    assert observed[2][0] == "reconcile"
    assert observed[2][2] == state
    assert observed[3] == ("workflow", ("app",))
    assert observed[4] == ("lease-exit", state)



def test_frontdoor_lease_blocks_control_state_reset_until_workflow_finishes(
    tmp_path, monkeypatch
) -> None:
    from oh_goon.services import certification_bootstrap

    source_root = tmp_path / "source"
    source_root.mkdir()
    state_home = tmp_path / "state-home"
    monkeypatch.chdir(source_root)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setattr(
        certification_frontdoor,
        "preflight_certification_database_under_lease",
        lambda **_kwargs: {"database": "admitted"},
    )

    observed: list[str] = []

    def workflow(_args: list[str]) -> int:
        try:
            certification_bootstrap.reset_certification_control_state(
                source_root=source_root,
                state_home=state_home,
            )
        except certification_bootstrap.CertificationStateBusy:
            observed.append("reset-blocked")
            return 0
        raise AssertionError("reset must not acquire certification state while workflow owns it")

    monkeypatch.setattr(certification_frontdoor, "_workflow_main", workflow)

    assert certification_frontdoor._workflow_main_with_state_lease(["app"]) == 0
    assert observed == ["reset-blocked"]

def test_static_source_failure_blocks_certification_before_workflow(monkeypatch, capsys) -> None:
    monkeypatch.setattr(certification_frontdoor, "_static_source_admission", lambda: False)
    monkeypatch.setattr(
        certification_frontdoor,
        "_workflow_main",
        lambda _args: (_ for _ in ()).throw(AssertionError("workflow must not start")),
    )
    assert certification_frontdoor.main([]) == 2


def test_static_source_admission_rejects_transport_contamination_before_compiler(
    tmp_path, monkeypatch, capsys
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / "repair.patch").write_text("transport only\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        certification_frontdoor,
        "compile_canonical_goons",
        lambda _root: (_ for _ in ()).throw(AssertionError("compiler must not run")),
    )

    assert _REAL_STATIC_SOURCE_ADMISSION() is False
    output = capsys.readouterr().out
    assert "CERTIFICATION SOURCE ADMISSION FAILED" in output
    assert "repair.patch" not in output  # bounded normal diagnostics
    assert "1 disposable source transport artifact contaminate source" in output
    assert "No certification state or Fleet Run was mutated." in output


def test_static_source_admission_uses_in_process_compiler_authority(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/uv.lock").write_text("version = 1\n", encoding="utf-8")
    observed: list[Path] = []

    def compile_source(root: Path) -> dict[str, object]:
        observed.append(root)
        return {
            "status": "PASS",
            "goon_count": 42,
            "step_count": 52,
            "requirements_identity": "sha256:req",
            "execution_authority_identity": "sha256:exec",
            "authority_closures_proven": 43,
            "authority_closure_count": 43,
            "unresolved_execution_edge_count": 0,
            "package_executable_authority_count": 6,
            "issues": [],
        }

    monkeypatch.setattr(certification_frontdoor, "compile_canonical_goons", compile_source)
    monkeypatch.setattr(certification_frontdoor.sys, "executable", "/uv/base/python")

    assert _REAL_STATIC_SOURCE_ADMISSION() is True
    assert observed == [tmp_path]
    output = capsys.readouterr().out
    assert "CANONICAL GOON STATIC ADMISSION" in output
    assert "Requirements: sha256:req" in output
    assert "Execution authority: sha256:exec" in output
    assert "Authority closures: 43/43 proven" in output


def test_static_source_admission_preserves_compiler_failure_output(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/uv.lock").write_text("version = 1\n", encoding="utf-8")

    def compile_source(_root: Path) -> dict[str, object]:
        return {
            "status": "FAIL",
            "goon_count": 42,
            "step_count": 52,
            "requirements_identity": "sha256:test",
            "issues": [
                {
                    "code": "BROKEN",
                    "goon": "python/test",
                    "step_id": "unit",
                    "message": "canonical source",
                }
            ],
        }

    monkeypatch.setattr(certification_frontdoor, "compile_canonical_goons", compile_source)

    assert _REAL_STATIC_SOURCE_ADMISSION() is False
    output = capsys.readouterr().out
    assert "CERTIFICATION STATIC ADMISSION FAILED" in output
    assert "BROKEN python/test/unit: canonical source" in output
    assert "No certification state or Fleet Run was mutated." in output


def test_real_static_admission_rejects_transitive_missing_tool_before_workflow_state(
    tmp_path, monkeypatch, capsys
) -> None:
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "repo"
    shutil.copytree(source / "templates" / "goons", root / "templates" / "goons")
    shutil.copytree(source / "scripts", root / "scripts")
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    shutil.copy2(source / "backend" / "goon-execution-requirements.json", root / "backend")
    (root / "backend/src/oh_goon").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        source / "backend/src/oh_goon/canonical_goon_tool_authority.json",
        root / "backend/src/oh_goon",
    )
    shutil.copy2(source / "backend" / "uv.lock", root / "backend")
    shutil.copy2(source / "frontend" / "package.json", root / "frontend")
    shutil.copy2(source / "frontend" / "package-lock.json", root / "frontend")
    shutil.copy2(source / "Makefile", root / "Makefile")
    registry = root / "backend/goon-execution-requirements.json"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["workload_tool_permissions"]["make:verify-p0"].remove("uv")
    registry.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    monkeypatch.chdir(root)

    assert _REAL_STATIC_SOURCE_ADMISSION() is False

    output = capsys.readouterr().out
    assert "CERTIFICATION STATIC ADMISSION FAILED" in output
    assert "Missing tools: uv" in output
    assert "Required by: verify-p0 ->" in output
    assert "-> uv" in output
    assert "No certification state or Fleet Run was mutated." in output


def test_candidate_added_native_tool_cannot_self_authorize_before_fleet_state(
    tmp_path, monkeypatch, capsys
) -> None:
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "repo"
    shutil.copytree(source / "templates" / "goons", root / "templates" / "goons")
    shutil.copytree(source / "scripts", root / "scripts")
    (root / "backend/src/oh_goon").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    shutil.copy2(source / "backend/goon-execution-requirements.json", root / "backend")
    shutil.copy2(
        source / "backend/src/oh_goon/canonical_goon_tool_authority.json",
        root / "backend/src/oh_goon",
    )
    shutil.copy2(source / "backend/uv.lock", root / "backend")
    shutil.copy2(source / "frontend/package.json", root / "frontend")
    shutil.copy2(source / "frontend/package-lock.json", root / "frontend")
    makefile = (source / "Makefile").read_text(encoding="utf-8")
    makefile = makefile.replace(
        "# OH_GOON_OPAQUE_TOOLS: git ldd uv\nverify-p0:",
        "# OH_GOON_OPAQUE_TOOLS: curl git ldd uv\nverify-p0:",
        1,
    )
    (root / "Makefile").write_text(makefile, encoding="utf-8")
    monkeypatch.chdir(root)

    assert _REAL_STATIC_SOURCE_ADMISSION() is False

    output = capsys.readouterr().out
    assert "CERTIFICATION STATIC ADMISSION FAILED" in output
    assert "TOOL_PERMISSION_MISSING" in output
    assert "curl" in output
    assert "No certification state or Fleet Run was mutated." in output


def test_certification_process_environment_forces_rollback_journal_policy(monkeypatch) -> None:
    monkeypatch.setattr(certification_frontdoor.os, "name", "nt")

    observed = certification_frontdoor._certification_process_environment(
        {"OH_GOON_SQLITE_JOURNAL_MODE": "WAL", "PATH": "/usr/bin"}
    )

    assert observed["OH_GOON_SQLITE_JOURNAL_MODE"] == "DELETE"


def test_leased_frontdoor_preflight_failure_blocks_workflow(tmp_path, monkeypatch, capsys) -> None:
    from contextlib import contextmanager

    state = tmp_path / "cert-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    monkeypatch.setattr(
        certification_frontdoor,
        "certification_state_root",
        lambda *, source_root, state_home: state,
    )

    @contextmanager
    def lease(_path: Path):
        yield

    monkeypatch.setattr(certification_frontdoor, "certification_state_lease", lease)
    monkeypatch.setattr(
        certification_frontdoor,
        "preflight_certification_database_under_lease",
        lambda **_kwargs: (_ for _ in ()).throw(
            certification_frontdoor.CertificationEnvironmentError("corrupt sqlite")
        ),
    )
    monkeypatch.setattr(
        certification_frontdoor,
        "_workflow_main",
        lambda _args: (_ for _ in ()).throw(AssertionError("workflow must not start")),
    )

    assert certification_frontdoor._workflow_main_with_state_lease(["app"]) == 2
    output = capsys.readouterr().out
    assert "CERTIFICATION DATABASE PREFLIGHT FAILED" in output
    assert "corrupt sqlite" in output
    assert "No certification Goon ran." in output
