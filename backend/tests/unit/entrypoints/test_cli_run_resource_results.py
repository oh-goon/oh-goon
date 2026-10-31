"""Parent result handling with native launch/observation adapters replaced explicitly."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

import oh_goon.cli_run_resources as resource_cli
from oh_goon.core.errors import JournalConflict
from oh_goon.core.models.run_resources import RunResourceEnvelopeObservation
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.vocabulary import FleetRunStatus
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.run_resource_envelope import ResolvedRunResourceScope
from oh_goon.runtime.database import ExistingDatabaseAppendContext
from oh_goon.runtime.journal import Journal, RunResourceObservationContext
from oh_goon.services.run_configuration import load_run_configuration
from tests.factories.run_resources import run_resource_envelope, run_resource_observation

pytestmark = pytest.mark.skipif(
    not atomic_execution_bytes_supported()
    or not hasattr(os, "pidfd_open")
    or not hasattr(signal, "pidfd_send_signal"),
    reason="Linux sealed transport and pidfd unavailable",
)
ResultRunner = Callable[..., tuple[int, dict[str, Any]]]


@pytest.fixture
def result_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> ResultRunner:
    expected = run_resource_envelope()
    configuration = tmp_path / "run.yml"
    configuration.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": "actual-subject"},
                "resources": expected.resources.model_dump(),
            }
        )
    )
    snapshot = load_run_configuration(configuration)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, "DELETE")
    executable = str(Path(sys.executable).resolve())
    scope = ResolvedRunResourceScope(
        expected.fleet_run_id,
        expected.resources,
        expected.unit_name,
        executable,
        "a" * 64,
        executable,
        "a" * 64,
        (),
        (
            executable,
            "--",
        ),
    )
    monkeypatch.setattr(resource_cli, "prepare_run_resource_scope", lambda *a: scope)
    context = RunResourceObservationContext(
        database=ExistingDatabaseAppendContext(
            path=tmp_path / "state.db",
            device=1,
            inode=2,
            filesystem=None,
            schema_cookie=1,
            database_identity_cookie=1,
            journal_mode="DELETE",
            durable_revision=3,
            session_id="original-child",
        ),
        expected=expected,
        journal_revision=3,
        journal_hash="b" * 64,
        last_run_sequence=3,
        terminal_status=FleetRunStatus.COMPLETED,
    )
    observation = run_resource_observation(expected)

    def run(
        *,
        stdout: str | None = None,
        exit_code: int = 0,
        truncated: bool = False,
        persistence_error: Exception | None = None,
        command_error: Exception | None = None,
        finish_error: Exception | None = None,
    ) -> tuple[int, dict[str, Any]]:
        receipt = {
            "schema": "oh-goon.plan-run-result/v1",
            "fleet_run_id": expected.fleet_run_id,
            "status": "COMPLETED" if exit_code == 0 else "FAILED",
            "outcome": "SUCCESS" if exit_code == 0 else "ATTENTION_REQUIRED",
        }

        def execute(*_args: str, **options: Any) -> CommandResult:
            if command_error is not None:
                raise command_error
            packet = json.loads(
                os.pread(options["pass_fds"][0], resource_cli.MAX_CLI_RESOURCE_HANDOFF_BYTES, 0)
            )
            terminal = Path(packet["observation_path"]).with_name("terminal.json")
            terminal.write_text(context.model_dump_json())
            terminal.chmod(0o600)
            return CommandResult(
                exit_code,
                json.dumps(receipt) if stdout is None else stdout,
                "",
                stdout_truncated=truncated,
            )

        def finish(parent: Any) -> RunResourceEnvelopeObservation:
            parent.started = expected
            if finish_error is not None:
                raise finish_error
            return observation

        def record(
            received: RunResourceObservationContext, observed: RunResourceEnvelopeObservation
        ) -> None:
            assert received == context
            if persistence_error is not None:
                raise persistence_error

        monkeypatch.setattr(resource_cli, "run_command", execute)
        monkeypatch.setattr(resource_cli._RunResourceParent, "finish", finish)
        monkeypatch.setattr(Journal, "record_run_resource_observation_from_context", record)
        code = resource_cli.run_with_resource_envelope(
            argparse.Namespace(fleet_run_id=None, as_json=True),
            snapshot,
            ["run", str(configuration), "--json"],
        )
        return code, json.loads(capsys.readouterr().out)

    return run


def test_optional_native_metrics_do_not_rewrite_completed_child_result(
    result_runner: ResultRunner,
) -> None:
    code, receipt = result_runner()
    assert code == 0
    assert receipt["status"] == "COMPLETED"
    assert receipt["resource_observation"]["state"] == "INCOMPLETE"
    assert receipt["resource_observation"]["cleanup"] == "VERIFIED"
    assert receipt["resource_observation_persistence"] == "RECORDED"


@pytest.mark.parametrize(
    "stdout,truncated", [("invalid", False), ("[]", False), ("{}", False), (None, True)]
)
def test_missing_or_truncated_child_result_cannot_report_success(
    result_runner: ResultRunner, stdout: str | None, truncated: bool
) -> None:
    assert result_runner()[0] == 0
    code, receipt = result_runner(stdout=stdout, truncated=truncated)
    assert code == 2
    assert receipt["status"] == "incomplete"
    assert receipt["admission_status"] == "UNCONFIRMED"
    assert "ORIGINAL_CLI_RESULT_UNAVAILABLE" in receipt["resource_cleanup_errors"]


def test_exact_head_conflict_is_separate_from_original_success(result_runner: ResultRunner) -> None:
    assert result_runner()[0] == 0
    code, receipt = result_runner(persistence_error=JournalConflict("changed head"))
    assert code == 2
    assert receipt["status"] == "COMPLETED"
    assert receipt["resource_observation_persistence"] == "INCOMPLETE"


def test_native_cleanup_failure_preserves_nonzero_workload_result(
    result_runner: ResultRunner,
) -> None:
    assert result_runner(exit_code=7)[0] == 7
    code, receipt = result_runner(exit_code=7, finish_error=OSError("cleanup unavailable"))
    assert code == 7
    assert receipt["status"] == "FAILED"
    assert receipt["resource_observation"]["cleanup"] == "INCOMPLETE"


def test_primary_command_exception_survives_cleanup_failure(result_runner: ResultRunner) -> None:
    original = RuntimeError("original command failure")
    with pytest.raises(RuntimeError) as raised:
        result_runner(command_error=original, finish_error=OSError("cleanup unavailable"))
    assert raised.value is original
    assert "RUN_RESOURCE_FINISH_INCOMPLETE" in original.__notes__
