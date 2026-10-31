from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_runtime_state import run_basic_doctor, run_runtime_doctor
from oh_goon.core.errors import DatabaseSchemaMismatch
from oh_goon.core.models.recovery import InvariantSeverity, PlainInvariantFinding
from oh_goon.core.models.runtime_safety import DegradedRuntimeSafetyProof, ProvenRuntimeSafetyProof
from oh_goon.core.models.tool_health import MissingRequiredToolHealth, ReadyToolHealth, SystemHealth
from oh_goon.core.vocabulary import RuntimeSafetyStatus, ToolStatus
from oh_goon.runtime.database import (
    DATABASE_SCHEMA_VERSION,
    DatabasePathIdentityLost,
    LocalDatabase,
)
from oh_goon.services.system_health import SystemHealthService


def _tools() -> tuple[ReadyToolHealth, ...]:
    return tuple(
        ReadyToolHealth(
            name=name, required=True, status=ToolStatus.READY, version=f"{name} fixture"
        )
        for name in ("git", "uv")
    )


def _paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {
        "database": tmp_path / "control/store.sqlite",
        "league": tmp_path / "selected-league.yml",
        "providers": tmp_path / "selected-providers.json",
    }
    for name, path in paths.items():
        monkeypatch.setenv(
            {
                "database": "OH_GOON_DB",
                "league": "OH_GOON_LEAGUE_FILE",
                "providers": "OH_GOON_PROVIDERS_FILE",
            }[name],
            str(path),
        )
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    return paths


@pytest.mark.parametrize("state", ["missing", "corrupt", "incompatible", "read_only"])
def test_basic_doctor_reports_selected_paths_without_opening_state_or_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
) -> None:
    paths = _paths(tmp_path, monkeypatch)
    database = paths["database"]
    database.parent.mkdir()
    if state == "corrupt":
        database.write_bytes(b"not a SQLite database")
    elif state in {"incompatible", "read_only"}:
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE retained_work(value TEXT)")
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")
        if state == "read_only":
            database.chmod(0o400)
            database.parent.chmod(0o500)
    before = database.read_bytes() if database.exists() else None
    canary = "DOCTOR_PRIVATE_CONFIGURATION_CANARY"
    paths["league"].write_text(canary)
    paths["providers"].write_text(canary)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Basic doctor crossed a state/configuration/connection boundary")

    monkeypatch.setattr(ApplicationRuntime, "build", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr("oh_goon.runtime.agent_providers.load_agent_providers", forbidden)
    monkeypatch.setattr("oh_goon.runtime.league_folders.LeagueFolderStore.list", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr(SystemHealthService, "inspect_tools", classmethod(lambda cls: _tools()))
    try:
        assert run_basic_doctor(argparse.Namespace(as_json=True)) == 0
        output = capsys.readouterr()
        report = json.loads(output.out)
        assert output.err == "" and canary not in output.out
        assert report["scope"] == "tools" and report["status"] == "ready"
        assert report["paths"] == {
            **{name: str(path) for name, path in paths.items()},
            "application_state": str(database.parent),
        }
        assert report["configured_roots"]["XDG_STATE_HOME"] == str(tmp_path / "state")
        assert report["implementation"]["python_executable"] == sys.executable
        assert (database.read_bytes() if database.exists() else None) == before
        assert sorted(path.name for path in database.parent.iterdir()) == (
            [] if before is None else [database.name]
        )
    finally:
        database.parent.chmod(0o700)
        if database.exists():
            database.chmod(0o600)


def test_basic_doctor_missing_tool_preserves_tool_reason_and_path_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        SystemHealthService,
        "inspect_tools",
        classmethod(lambda cls: (MissingRequiredToolHealth(name="uv", status=ToolStatus.MISSING),)),
    )
    assert run_basic_doctor(argparse.Namespace(as_json=False)) == 2
    output = capsys.readouterr().out
    assert "MISSING" in output and str(paths["database"]) in output
    assert "doctor --runtime" in output
    assert not paths["database"].parent.exists()


@pytest.mark.parametrize(
    "failure,code",
    [
        (DatabaseSchemaMismatch("private schema detail"), "STATE_SCHEMA_INCOMPATIBLE"),
        (DatabasePathIdentityLost("private identity detail"), "STATE_IDENTITY_UNAVAILABLE"),
        (PermissionError("private access detail"), "STATE_OBSERVATION_UNAVAILABLE"),
        (ModuleNotFoundError("private import detail"), "STATE_OBSERVATION_UNAVAILABLE"),
    ],
)
def test_runtime_doctor_refusal_uses_observation_only_startup_and_preserves_private_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    code: str,
) -> None:
    paths = _paths(tmp_path, monkeypatch)

    def unavailable(**arguments: object) -> ApplicationRuntime:
        assert arguments == {"startup_purpose": "observation"}
        raise failure

    monkeypatch.setattr(ApplicationRuntime, "build", unavailable)
    monkeypatch.setattr(SystemHealthService, "inspect_tools", classmethod(lambda cls: _tools()))
    assert run_runtime_doctor(argparse.Namespace(as_json=True)) == 2
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert output.err == "" and str(failure) not in output.out
    assert report["scope"] == "runtime" and report["status"] == "blocked"
    assert report["code"] == code and "Preserve existing state" in report["safe_next_action"]
    assert report["paths"]["database"] == str(paths["database"])
    assert not paths["database"].parent.exists()


@pytest.mark.parametrize("degraded,cleanup_fails", [(False, False), (True, False), (False, True)])
def test_runtime_doctor_reports_original_health_after_closing_observer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    degraded: bool,
    cleanup_fails: bool,
) -> None:
    _paths(tmp_path, monkeypatch)
    proof = (
        DegradedRuntimeSafetyProof(
            status=RuntimeSafetyStatus.DEGRADED,
            durable_revision=7,
            journal_revision=3,
            findings=(
                PlainInvariantFinding(
                    finding_type="PLAIN",
                    severity=InvariantSeverity.ERROR,
                    code="retained-owner-unresolved",
                    message="Retained original ownership remains unresolved",
                ),
            ),
        )
        if degraded
        else ProvenRuntimeSafetyProof(
            status=RuntimeSafetyStatus.PROVEN_SAFE, durable_revision=7, journal_revision=3
        )
    )
    health = SystemHealth(tools=_tools(), runtime_safety=proof, background_failures=())
    closed: list[bool] = []

    def shutdown(*, wait_for_runs: bool) -> None:
        assert not wait_for_runs
        assert capsys.readouterr().out == "", "diagnosis must not publish before observer teardown"
        closed.append(True)
        if cleanup_fails:
            raise OSError("private cleanup detail")

    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(system_health=SimpleNamespace(inspect=lambda: health), shutdown=shutdown),
    )

    def build(**arguments: object) -> ApplicationRuntime:
        assert arguments == {"startup_purpose": "observation"}
        return runtime

    monkeypatch.setattr(ApplicationRuntime, "build", build)
    assert run_runtime_doctor(argparse.Namespace(as_json=True)) == (
        2 if degraded or cleanup_fails else 0
    )
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert output.err == "" and "private cleanup detail" not in output.out
    assert report["runtime_safety"] == proof.model_dump(mode="json")
    assert report["status"] == health.status.value and closed == [True]
    if cleanup_fails:
        assert report["cleanup"] == "INCOMPLETE"


@pytest.mark.process
@pytest.mark.parametrize("state", ["missing", "corrupt", "incompatible", "ready", "read_only"])
def test_fresh_cli_doctor_keeps_database_bytes_and_never_initializes_missing_state(
    tmp_path: Path, state: str
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    database = control / "store.sqlite"
    if state == "corrupt":
        database.write_bytes(b"invalid SQLite")
    elif state == "incompatible":
        with sqlite3.connect(database) as connection:
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")
    elif state in {"ready", "read_only"}:
        LocalDatabase(database)
        if state == "read_only":
            database.chmod(0o400)
    before = database.read_bytes() if database.exists() else None
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(database),
        OH_GOON_LEAGUE_FILE=str(control / "league.yml"),
        XDG_CONFIG_HOME=str(control / "config"),
        XDG_DATA_HOME=str(control / "data"),
        XDG_STATE_HOME=str(control / "state"),
        XDG_CACHE_HOME=str(control / "cache"),
    )
    for arguments in (("doctor", "--json"), ("doctor", "--runtime", "--json")):
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "from oh_goon.cli import main; raise SystemExit(main())",
                *arguments,
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert len(completed.stdout) < 65_536 and len(completed.stderr) < 65_536
        report = json.loads(completed.stdout)
        if "--runtime" in arguments:
            assert completed.returncode == (0 if state in {"ready", "read_only"} else 2)
            assert report["scope"] == "runtime"
            if state not in {"ready", "read_only"}:
                assert report["status"] == "blocked" and report["code"].startswith("STATE_")
        else:
            assert completed.returncode == 0 and report["scope"] == "tools"
        assert report["paths"]["database"] == str(database)
        assert (database.read_bytes() if database.exists() else None) == before
        if state == "read_only" and os.name == "posix":
            assert database.stat().st_mode & 0o777 == 0o400
        assert not (control / "league.yml").exists()
        assert not (control / "content-store").exists()
        assert not (control / "diagnostics").exists()
