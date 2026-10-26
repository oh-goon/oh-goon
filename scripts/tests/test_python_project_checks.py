from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandResult,
    CommandSupervisorReceipt,
)
from oh_goon.repository_tooling import python_project_checks


def test_failed_native_project_check_cleans_owned_environment_and_preserves_developer_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "owned"
    scratch.mkdir()
    developer = tmp_path / "templates/goons/ml/iris-train/.venv/developer"
    developer.parent.mkdir(parents=True)
    developer.write_text("preserved")
    monkeypatch.setattr(
        python_project_checks.tempfile, "mkdtemp", lambda **_: str(scratch)
    )

    def failed(*_arguments: str, env: dict[str, str], **_kwargs: Any) -> CommandResult:
        environment = Path(env["UV_PROJECT_ENVIRONMENT"])
        assert environment.is_relative_to(scratch)
        environment.mkdir()
        (environment / "partial-install").write_text("owned")
        return CommandResult(7, "native failure", "")

    monkeypatch.setattr(python_project_checks, "run_command", failed)
    assert python_project_checks.run_project_tests(tmp_path) == 7
    assert not scratch.exists()
    assert developer.read_text() == "preserved"


@pytest.mark.parametrize("quiescent", [False, True])
def test_native_project_cancellation_respects_process_cleanup_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    quiescent: bool,
) -> None:
    scratch = tmp_path / "owned"
    scratch.mkdir()
    monkeypatch.setattr(
        python_project_checks.tempfile, "mkdtemp", lambda **_: str(scratch)
    )

    def cancelled(
        *_arguments: str, process_observer: Any, **_kwargs: Any
    ) -> CommandResult:
        process_observer(CommandProcessLifecycleEvent("PROCESS_STARTING", "process"))
        (scratch / "partial-install").write_text("owned")
        raise CommandCancelledError(
            "cancelled",
            supervisor=CommandSupervisorReceipt(
                final_process_group_quiescent=quiescent
            ),
        )

    monkeypatch.setattr(python_project_checks, "run_command", cancelled)
    with pytest.raises(CommandCancelledError, match="cancelled"):
        python_project_checks.run_project_tests(tmp_path)
    assert scratch.exists() is not quiescent
    if not quiescent:
        assert "process cleanup is unproven" in capsys.readouterr().err
        shutil.rmtree(scratch)


def test_project_cleanup_failure_does_not_replace_native_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch = tmp_path / "owned"
    scratch.mkdir()
    monkeypatch.setattr(
        python_project_checks.tempfile, "mkdtemp", lambda **_: str(scratch)
    )
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *_args, **_kwargs: CommandResult(7, "", ""),
    )

    def deny_cleanup(_path: Path) -> None:
        raise PermissionError("busy")

    monkeypatch.setattr(python_project_checks.shutil, "rmtree", deny_cleanup)
    assert python_project_checks.run_project_tests(tmp_path) == 7
    assert "environment cleanup failed" in capsys.readouterr().err
