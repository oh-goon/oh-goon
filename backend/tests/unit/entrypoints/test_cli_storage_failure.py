from __future__ import annotations

import argparse
import builtins
import json
import sqlite3
from typing import Any

import pytest

from oh_goon import cli, cli_runtime
from oh_goon.application import ApplicationRuntime
from oh_goon.runtime.database import DatabasePathIdentityLost


class _Parser:
    def parse_args(self, _argv: list[str] | None) -> argparse.Namespace:
        return argparse.Namespace(command="plan", plan_command=None)

    def print_help(self) -> None:
        raise AssertionError("help must not replace storage failure")


class _Runtime:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self, *, wait_for_runs: bool = False) -> None:
        assert wait_for_runs is False
        self.shutdown_calls += 1
        raise sqlite3.OperationalError("unable to open database file")


def _prepare(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime) -> None:
    monkeypatch.setattr(cli, "build_parser", lambda: _Parser())
    monkeypatch.setattr(cli, "run_local_state_command", lambda _args: None)
    monkeypatch.setattr(cli, "run_start_command", lambda _args: None)
    monkeypatch.setattr(cli, "run_enforcer_compare_command", lambda _args: None)

    def build(_cls: type[ApplicationRuntime], /, *, standard_local: bool | None = None) -> _Runtime:
        return runtime

    monkeypatch.setattr(ApplicationRuntime, "build", classmethod(build))


def test_cli_reports_primary_runtime_storage_loss_once_and_shutdown_cannot_mask_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(
        cli_runtime,
        "run_stateful_command",
        lambda _runtime, _args: (_ for _ in ()).throw(
            sqlite3.OperationalError("unable to open database file")
        ),
    )

    assert cli.main([]) == 2
    output = capsys.readouterr().out
    assert output.count("OH-GOON RUNTIME STORAGE UNAVAILABLE") == 1
    assert "environment/storage failure" in output
    assert runtime.shutdown_calls == 1


def test_cli_turns_storage_loss_during_shutdown_into_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(cli_runtime, "run_stateful_command", lambda _runtime, _args: 0)

    assert cli.main([]) == 2
    output = capsys.readouterr().out
    assert output.count("OH-GOON RUNTIME STORAGE UNAVAILABLE") == 1
    assert runtime.shutdown_calls == 1


def test_cli_reports_database_identity_replacement_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(runtime, "shutdown", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli_runtime,
        "run_stateful_command",
        lambda _runtime, _args: (_ for _ in ()).throw(
            DatabasePathIdentityLost("database path was replaced after runtime admission")
        ),
    )

    assert cli.main([]) == 2
    output = capsys.readouterr().out
    assert "OH-GOON RUNTIME DATABASE IDENTITY LOST" in output
    assert "bounded observation-only checks" in output
    assert (
        "will not recreate/repair the database or replay a Plan, Step, or external effect" in output
    )
    assert "preserve this diagnostic before using make certification-state-reset" in output
    assert "source/storage defect" in output
    assert (
        "Native package-manager state is outside certification control-state reset authority"
        in output
    )


def test_cli_preserves_nonzero_primary_result_when_shutdown_loses_database_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(cli_runtime, "run_stateful_command", lambda _runtime, _args: 7)
    monkeypatch.setattr(
        runtime,
        "shutdown",
        lambda **_kwargs: (_ for _ in ()).throw(
            DatabasePathIdentityLost("file is not a database: PRIVATE_DATABASE_IDENTITY_CANARY")
        ),
    )

    assert cli.main([]) == 7
    captured = capsys.readouterr()
    output = captured.out
    assert output.count("OH-GOON RUNTIME DATABASE IDENTITY LOST") == 1
    assert "file is not a database" not in output + captured.err
    assert "PRIVATE_DATABASE_IDENTITY_CANARY" not in output + captured.err


def test_cli_closes_runtime_when_stateful_dispatch_loading_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    original_import = builtins.__import__

    def unavailable_dispatch(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "oh_goon.cli_runtime":
            raise ModuleNotFoundError("PRIVATE_DISPATCH_IMPORT_CANARY", name=name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable_dispatch)
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.count("CLI_COMMAND_FAILED") == 1
    assert "PRIVATE_DISPATCH_IMPORT_CANARY" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert runtime.shutdown_calls == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        (
            DatabasePathIdentityLost("PRIVATE_DATABASE_PATH identity changed"),
            "STATE_IDENTITY_UNAVAILABLE",
        ),
        (
            sqlite3.OperationalError("database or disk is full: PRIVATE_DATABASE_PATH"),
            "STATE_OBSERVATION_UNAVAILABLE",
        ),
    ],
)
def test_run_readback_reports_lost_authority_as_one_safe_json_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    code: str,
) -> None:
    runtime = _Runtime()

    def build(_cls: type[ApplicationRuntime], /, **kwargs: Any) -> _Runtime:
        assert kwargs == {"startup_purpose": "observation"}
        return runtime

    monkeypatch.setattr(ApplicationRuntime, "build", classmethod(build))
    monkeypatch.setattr(
        cli_runtime,
        "run_stateful_command",
        lambda _runtime, _args: (_ for _ in ()).throw(failure),
    )

    assert cli.main(["runs", "show", "recorded-run", "--json"]) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["code"] == code
    assert report["fleet_run_id"] == "recorded-run"
    assert report["status"] == "error"
    assert captured.err == "" and "PRIVATE_DATABASE_PATH" not in captured.out
    assert runtime.shutdown_calls == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        (
            DatabasePathIdentityLost("PRIVATE_POST_RESULT_CLEANUP_CANARY"),
            "RUNTIME_DATABASE_IDENTITY_LOST",
        ),
        (
            sqlite3.OperationalError(
                "unable to open database file: PRIVATE_POST_RESULT_CLEANUP_CANARY"
            ),
            "RUNTIME_STORAGE_UNAVAILABLE",
        ),
    ],
)
def test_json_cleanup_failure_keeps_original_successful_owner_payload_and_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    code: str,
) -> None:
    runtime = _Runtime()
    owner_payload = {
        "schema": "original-run-owner/v1",
        "status": "COMPLETED",
        "fleet_run_id": "recorded-run",
        "outcome": "SUCCESS",
    }

    def build(_cls: type[ApplicationRuntime], /, **arguments: Any) -> _Runtime:
        assert arguments == {"startup_purpose": "observation"}
        return runtime

    def emit_result(_runtime: object, _args: argparse.Namespace) -> int:
        print(json.dumps(owner_payload))
        return 0

    def failed_cleanup(*, wait_for_runs: bool) -> None:
        assert not wait_for_runs
        runtime.shutdown_calls += 1
        raise failure

    monkeypatch.setattr(ApplicationRuntime, "build", classmethod(build))
    monkeypatch.setattr(cli_runtime, "run_stateful_command", emit_result)
    monkeypatch.setattr(runtime, "shutdown", failed_cleanup)

    assert cli.main(["runs", "show", "recorded-run", "--json"]) == 2

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert {name: report[name] for name in owner_payload} == owner_payload
    assert report["_cli"] == {
        "schema": "oh-goon.cli/v1",
        "exit_code": 2,
        "exit_kind": "COMMAND_ERROR",
        "cleanup": {"status": "UNVERIFIED", "code": code},
    }
    assert "PRIVATE_POST_RESULT_CLEANUP_CANARY" not in captured.out + captured.err
    assert captured.err == "" and runtime.shutdown_calls == 1
