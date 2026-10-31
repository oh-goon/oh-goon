from __future__ import annotations

import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandResult,
    CommandSupervisorReceipt,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.repository_tooling import python_project_checks


def test_required_project_checks_select_frozen_application_tests_without_airflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OH_GOON_PYTEST_REPORT_PATH", str(tmp_path / "parent.json"))
    monkeypatch.setenv(
        "OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY", str(tmp_path / "parent-projects")
    )
    monkeypatch.setenv(
        "OH_GOON_VITEST_JSON_REPORT", str(tmp_path / "parent-vitest.json")
    )
    monkeypatch.setenv("PYTEST_REPORT_PATH", "caller-owned")
    commands: list[tuple[str, ...]] = []

    def complete(*arguments: str, **_kwargs: Any) -> CommandResult:
        environment = _kwargs["env"]
        assert environment["PYTEST_REPORT_PATH"] == "caller-owned"
        assert not any(
            name in environment
            for name in (
                "OH_GOON_PYTEST_REPORT_PATH",
                "OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY",
                "OH_GOON_VITEST_JSON_REPORT",
            )
        )
        commands.append(arguments)
        return CommandResult(0, "", "")

    monkeypatch.setattr(python_project_checks, "run_command", complete)
    assert python_project_checks.run_project_tests(tmp_path) == 0
    synchronization = [command for command in commands if command[1] == "sync"]
    assert len(synchronization) == 2
    for command in synchronization:
        assert "--frozen" in command and "--locked" not in command
        assert command[command.index("--group") + 1] == "dev"
        assert "airflow" not in command
    orders_tests = next(
        command
        for command in commands
        if str(tmp_path / "templates/goons/general/orders-taskflow/tests") in command
    )
    assert (
        str(tmp_path / "templates/goons/general/orders-taskflow/tests") in orders_tests
    )
    assert not any("test_airflow_adapter" in argument for argument in orders_tests)


def test_failed_native_project_check_cleans_owned_environment_and_preserves_developer_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "owned"
    scratch.mkdir()
    developer = tmp_path / "templates/goons/ml/iris-train/.venv/developer"
    developer.parent.mkdir(parents=True)
    developer.write_text("preserved")
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(scratch))

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
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(scratch))

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
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(scratch))
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *_args, **_kwargs: CommandResult(7, "", ""),
    )

    def deny_cleanup(_path: Path) -> None:
        raise PermissionError("busy")

    monkeypatch.setattr(shutil, "rmtree", deny_cleanup)
    assert python_project_checks.run_project_tests(tmp_path) == 7
    assert "environment cleanup failed" in capsys.readouterr().err


@pytest.mark.parametrize("phase", ("sync", "pytest"))
def test_daily_projects_continue_independent_failures_and_preserve_native_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = tmp_path / "reports"
    commands: list[tuple[str, ...]] = []

    def run(*arguments: str, **_kwargs: Any) -> CommandResult:
        commands.append(arguments)
        failed = "iris-train" in arguments[
            arguments.index("--project") + 1
        ] and arguments[1] == ("sync" if phase == "sync" else "run")
        return CommandResult(7 if failed else 0, "", "")

    monkeypatch.setattr(python_project_checks, "run_command", run)
    assert (
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
        == 7
    )
    summary = json.loads((reports / "summary.json").read_text())
    assert summary["schema"] == python_project_checks.PROJECT_REPORT_SCHEMA
    assert summary["cleanup"] == "VERIFIED"
    assert summary["first_nonzero_exit_code"] == 7
    assert summary["projects"][0][phase + "_exit_code"] == 7
    assert summary["projects"][1]["pytest_exit_code"] == 0
    assert summary["projects"][1]["phase"] == "COMPLETE"
    assert len(commands) == (3 if phase == "sync" else 4)
    for command in commands:
        if command[1] == "run":
            assert "--maxfail=0" in command
            assert "xfail_strict=true" in command
            report = next(
                argument for argument in command if argument.startswith("--junitxml=")
            )
            assert Path(report.removeprefix("--junitxml=")).parent == reports


@pytest.mark.parametrize(
    "unsafe", ("inside-source", "not-private", "symlink", "existing")
)
def test_unsafe_report_destination_refuses_before_project_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = source / "reports" if unsafe == "inside-source" else tmp_path / "reports"
    if unsafe == "not-private":
        reports.mkdir(mode=0o755)
        reports.chmod(0o755)
    if unsafe == "symlink":
        original = tmp_path / "original"
        original.mkdir(mode=0o700)
        reports.symlink_to(original, target_is_directory=True)
    if unsafe == "existing":
        reports.mkdir(mode=0o700)
        (reports / "iris.junit.xml").write_text("previous native evidence")
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda **kw: pytest.fail("unsafe report started setup")
    )
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *a, **kw: pytest.fail("work started"),
    )
    with pytest.raises((OSError, RuntimeError, ValueError)):
        python_project_checks.run_project_tests(source, report_directory=reports)


@pytest.mark.parametrize("quiescent", (False, True))
def test_daily_timeout_continues_only_after_original_process_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiescent: bool,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = tmp_path / "reports"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: str(scratch))
    timeout = CommandTimeoutExpired(
        "uv",
        600,
        supervisor=CommandSupervisorReceipt(
            final_process_group_quiescent=quiescent,
        ),
    )
    calls = []

    def run(*arguments: str, **_kwargs: Any) -> CommandResult:
        calls.append(arguments)
        if len(calls) == 1:
            raise timeout
        return CommandResult(0, "", "")

    monkeypatch.setattr(python_project_checks, "run_command", run)
    with pytest.raises(CommandTimeoutExpired) as raised:
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
    assert raised.value is timeout
    assert len(calls) == (3 if quiescent else 1)
    summary = json.loads((reports / "summary.json").read_text())
    assert summary["projects"][0]["phase"] == "SYNC_TIMEOUT"
    assert summary["projects"][1]["phase"] == ("COMPLETE" if quiescent else "UNSTARTED")
    assert summary["cleanup"] == ("VERIFIED" if quiescent else "INCOMPLETE")
    if scratch.exists():
        shutil.rmtree(scratch)


def test_daily_user_cancel_preserves_uncovered_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    reports = tmp_path / "reports"
    calls = []

    def run(*arguments: str, **_kwargs: Any) -> CommandResult:
        calls.append(arguments)
        raise CommandCancelledError(
            "cancelled",
            supervisor=CommandSupervisorReceipt(
                final_process_group_quiescent=True,
            ),
        )

    monkeypatch.setattr(python_project_checks, "run_command", run)
    with pytest.raises(CommandCancelledError):
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
    assert len(calls) == 1
    summary = json.loads((reports / "summary.json").read_text())
    assert [project["phase"] for project in summary["projects"]] == [
        "CANCELLED",
        "UNSTARTED",
    ]


@pytest.mark.parametrize("native_exit,expected_exit", ((0, 1), (7, 7)))
def test_report_failure_never_rewrites_original_nonzero_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_exit: int,
    expected_exit: int,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(
        python_project_checks,
        "run_command",
        lambda *a, **kw: CommandResult(native_exit, "", ""),
    )

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(python_project_checks, "publish_text_file", fail)
    assert (
        python_project_checks.run_project_tests(
            source, report_directory=tmp_path / "reports"
        )
        == expected_exit
    )


@pytest.mark.process
@pytest.mark.skipif(shutil.which("uv") is None, reason="native uv required")
def test_native_tiny_projects_retain_failed_and_independent_passing_junit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    for name, relative, _tests in python_project_checks._PROJECTS:
        project = source / relative
        (project / "tests").mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            f'[project]\nname="tiny-{name}"\nversion="0.1.0"\nrequires-python=">=3.12"\n'
            f'[dependency-groups]\ndev=["pytest=={pytest.__version__}"]\n'
        )
        locked = run_command(
            "uv",
            "lock",
            "--offline",
            "--project",
            str(project),
            timeout=30,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=8192,
        )
        assert locked.returncode == 0, locked.stderr
        if name == "iris":
            (project / "tests/test_failure.py").write_text(
                "import pytest\n"
                "def test_failure(): assert False\n"
                "@pytest.mark.xfail(reason='expected native failure')\n"
                "def test_expected_failure(): assert False\n"
                "@pytest.mark.xfail(reason='unexpected native pass')\n"
                "def test_unexpected_pass(): assert True\n"
            )
        else:
            for test in ("test_operations", "test_cli"):
                (project / f"tests/{test}.py").write_text(
                    "def test_pass(): assert 1 + 1 == 2\n"
                )
    reports = tmp_path / "reports"
    assert (
        python_project_checks.run_project_tests(
            source,
            report_directory=reports,
            continue_independent=True,
        )
        == 1
    )
    summary = json.loads((reports / "summary.json").read_text())
    assert summary["cleanup"] == "VERIFIED"
    assert [project["pytest_exit_code"] for project in summary["projects"]] == [1, 0]
    for project, expected_count, expected_failure in zip(
        summary["projects"],
        (3, 2),
        (2, 0),
        strict=True,
    ):
        native = ET.parse(reports / project["junit_file"])
        assert len(native.findall(".//testcase")) == expected_count
        assert len(native.findall(".//failure")) == expected_failure
        if project["name"] == "iris":
            skipped = native.find(".//skipped")
            assert skipped is not None and skipped.get("type") == "pytest.xfail"
            assert any(
                failure.get("message", "").startswith("[XPASS(strict)]")
                for failure in native.findall(".//failure")
            )
