from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/development_check.sh"
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def _isolated_runner(tmp_path: Path) -> Path:
    scripts = tmp_path / "checkout/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    shutil.copy2(ROOT / "scripts/command_banner.sh", scripts / "command_banner.sh")
    owner = scripts.parent / "backend/.venv/bin/oh-goon-source-layout"
    owner.parent.mkdir(parents=True)
    owner.symlink_to(ROOT / "backend/.venv/bin/oh-goon-source-layout")
    return scripts / SCRIPT.name


def _native_frontdoor_commands(target: str, log_directory: Path) -> list[list[str]]:
    environment = os.environ.copy()
    for name in (
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
        "DEVELOPMENT_TEST_FRONTDOOR",
        "DEVELOPMENT_RESOURCE_RUNNER",
        "DEVELOPMENT_TEST_RUNNER",
        "DEVELOPMENT_CHECK_RUNNER",
        "APPLICATION_VERIFY_RUNNER",
    ):
        environment.pop(name, None)
    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "--dry-run",
            target,
            f"RUN_LOG_DIR={log_directory}",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )

    return [
        shlex.split(line)
        for line in completed.stdout.replace("\\\n", " ").splitlines()
        if line.strip()
    ]


@pytest.mark.parametrize(
    ("target", "runner"),
    (
        ("check", "scripts/development_check.sh"),
        ("test", "scripts/development_check.sh"),
        ("verify", "scripts/application_verify.sh"),
    ),
)
def test_logged_local_frontdoor_wraps_original_command_once(
    target: str, runner: str, tmp_path: Path
) -> None:
    log_directory = tmp_path / "run logs"
    commands = _native_frontdoor_commands(target, log_directory)
    assert commands == [
        [
            f"OH_GOON_RUN_LOG_DIR={log_directory}",
            "bash",
            "scripts/run_logged.sh",
            target,
            "bash",
            "scripts/development_test_resources.sh",
            "bash",
            runner,
        ]
    ]


def test_logged_certification_frontdoor_caps_the_original_workflow_once(
    tmp_path: Path,
) -> None:
    log_directory = tmp_path / "run logs"
    commands = _native_frontdoor_commands("certify", log_directory)
    frontdoors = [command for command in commands if "scripts/run_logged.sh" in command]
    assert frontdoors == [
        [
            f"OH_GOON_RUN_LOG_DIR={log_directory}",
            "bash",
            "scripts/run_logged.sh",
            "certify",
            "bash",
            "scripts/development_test_resources.sh",
            "backend/.venv/bin/oh-goon-workflow",
        ]
    ]


@pytest.mark.parametrize(
    ("target", "script", "arguments"),
    (
        ("test-frontend", "test", ()),
        ("frontend-functional-certification", "verify:functional", ()),
        ("frontend-check", "check", ()),
        ("browser-required-certification", "browser:test", ()),
        (
            "frontend-schedule-plan-input-regression",
            "test",
            (
                "--",
                "src/features/schedules/components/ScheduleCreatePanel.test.tsx",
                "src/features/schedules/components/schedulePlanInputDraft.test.ts",
            ),
        ),
    ),
)
def test_standalone_frontend_gates_cap_the_original_native_npm_command(
    target: str, script: str, arguments: tuple[str, ...], tmp_path: Path
) -> None:
    commands = _native_frontdoor_commands(target, tmp_path / "run logs")
    (command,) = [
        command
        for command in commands
        if "npm" in command
        and command[command.index("npm") + 1 :] == ["run", script, *arguments]
    ]
    assert command[:6] == [
        "bash",
        "scripts/development_test_resources.sh",
        "sh",
        "-c",
        'cd frontend && exec "$@"',
        "sh",
    ]


def test_certify_retains_its_unchanged_execution_authority() -> None:
    assert "certify:" in MAKEFILE
    assert "CERTIFICATION INITIALIZATION REQUIRED" in MAKEFILE
    assert 'test -x "$(CERTIFICATION_RUNNER)"' in MAKEFILE
    assert "\t@$(CERTIFICATION_FRONTDOOR)" in MAKEFILE
    assert "certify: goon-static-admission" not in MAKEFILE


def test_standalone_python_advisory_caps_both_pipeline_processes(
    tmp_path: Path,
) -> None:
    commands = _native_frontdoor_commands("python-static-sanity", tmp_path / "logs")
    (pipeline,) = [command for command in commands if "-c" in command]
    assert pipeline[:4] == [
        "bash",
        "scripts/development_test_resources.sh",
        "sh",
        "-c",
    ]
    assert (
        "ruff check" in pipeline[4]
        and "| .venv/bin/oh-goon-ruff-advisory" in pipeline[4]
    )
    assert pipeline[4].endswith("exit 0")


def test_fast_check_delegates_one_relevant_owner_without_archive_history(
    tmp_path: Path,
) -> None:
    script = _isolated_runner(tmp_path)
    calls = tmp_path / "calls"
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$OH_GOON_TEST_CALLS"\n'
        'test "$*" = "--no-print-directory change-check"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script)],
        cwd=script.parent.parent,
        env={**os.environ, "MAKE": str(fake), "OH_GOON_TEST_CALLS": str(calls)},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text().splitlines() == ["--no-print-directory change-check"]


def test_fast_check_retains_native_failure_and_starts_no_unrelated_owner(
    tmp_path: Path,
) -> None:
    script = _isolated_runner(tmp_path)
    calls = tmp_path / "calls"
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$OH_GOON_TEST_CALLS"\n'
        'exit "$OH_GOON_TEST_CHECK_STATUS"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    environment = {
        **os.environ,
        "MAKE": str(fake),
        "OH_GOON_TEST_CALLS": str(calls),
        "OH_GOON_TEST_CHECK_STATUS": "0",
    }
    valid = subprocess.run(
        ["bash", str(script)],
        cwd=script.parent.parent,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr
    calls.unlink()
    invalid = subprocess.run(
        ["bash", str(script)],
        cwd=script.parent.parent,
        env={**environment, "OH_GOON_TEST_CHECK_STATUS": "23"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert invalid.returncode == 1
    assert "exit 23" in invalid.stdout
    assert calls.read_text().splitlines() == ["--no-print-directory change-check"]
