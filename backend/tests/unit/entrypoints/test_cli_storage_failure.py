from __future__ import annotations

import argparse
import sqlite3

from oh_goon import cli
from oh_goon.runtime.database import DatabasePathIdentityLost


class _Parser:
    def parse_args(self, _argv):
        return argparse.Namespace(command="plan", plan_command=None)

    def print_help(self):
        raise AssertionError("help must not replace storage failure")


class _Runtime:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self, *, wait_for_runs: bool = False) -> None:
        assert wait_for_runs is False
        self.shutdown_calls += 1
        raise sqlite3.OperationalError("unable to open database file")


def _prepare(monkeypatch, runtime: _Runtime) -> None:
    monkeypatch.setattr(cli, "build_parser", lambda: _Parser())
    monkeypatch.setattr(cli, "run_local_state_command", lambda _args: None)
    monkeypatch.setattr(cli, "run_start_command", lambda _args: None)
    monkeypatch.setattr(cli, "run_enforcer_compare_command", lambda _args: None)
    monkeypatch.setattr(cli, "run_state_free_developer_command", lambda _args: None)

    def build(_cls, *, standard_local=None):
        return runtime

    monkeypatch.setattr(cli.ApplicationRuntime, "build", classmethod(build))


def test_cli_reports_primary_runtime_storage_loss_once_and_shutdown_cannot_mask_it(
    monkeypatch, capsys
) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(
        cli,
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


def test_cli_turns_storage_loss_during_shutdown_into_failure(monkeypatch, capsys) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(cli, "run_stateful_command", lambda _runtime, _args: 0)

    assert cli.main([]) == 2
    output = capsys.readouterr().out
    assert output.count("OH-GOON RUNTIME STORAGE UNAVAILABLE") == 1
    assert runtime.shutdown_calls == 1


def test_cli_reports_database_identity_replacement_without_traceback(monkeypatch, capsys) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(runtime, "shutdown", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
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
    monkeypatch, capsys
) -> None:
    runtime = _Runtime()
    _prepare(monkeypatch, runtime)
    monkeypatch.setattr(cli, "run_stateful_command", lambda _runtime, _args: 7)
    monkeypatch.setattr(
        runtime,
        "shutdown",
        lambda **_kwargs: (_ for _ in ()).throw(DatabasePathIdentityLost("file is not a database")),
    )

    assert cli.main([]) == 7
    output = capsys.readouterr().out
    assert output.count("OH-GOON RUNTIME DATABASE IDENTITY LOST") == 1
    assert "file is not a database" in output
