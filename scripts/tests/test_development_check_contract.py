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
        ("test", "scripts/development_test.sh"),
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


def test_fast_check_does_not_run_complete_or_browser_certification_targets() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "test-python-complete" not in text
    assert "test-tooling-complete" not in text
    assert "browser-targeted-certification" not in text
    assert "browser-required-certification" not in text
    assert "certification-setup" not in text
    assert "test-python" in text
    assert "test-tooling" in text


def test_fast_check_runs_normal_owners_without_local_checkpoint_history(
    tmp_path: Path,
) -> None:
    """An ordinary checkout must not fail because archive-only inputs are absent."""
    script = _isolated_runner(tmp_path)
    checkout = script.parent.parent
    calls = tmp_path / "calls"
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        'printf \'%s\\n\' "$target" >> "$OH_GOON_TEST_CALLS"\n'
        'case "$target" in phase-evidence-convergence-check|recovery-checkpoint) exit 23 ;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(script)],
        cwd=checkout,
        env={**os.environ, "MAKE": str(fake), "OH_GOON_TEST_CALLS": str(calls)},
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    invoked = set(calls.read_text(encoding="utf-8").splitlines())
    assert {
        "test-python",
        "test-tooling",
        "architecture",
        "check-generated",
        "test-frontend",
    } <= invoked
    assert not {"phase-evidence-convergence-check", "recovery-checkpoint"} & invoked


def test_fast_check_reports_multiple_independent_failures(tmp_path: Path) -> None:
    script = _isolated_runner(tmp_path)
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        'case "$target" in test-python) exit 4 ;; architecture) exit 5 ;; *) exit 0 ;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=script.parent.parent,
        env={**os.environ, "MAKE": str(fake)},
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 1
    assert "Backend fast unit" in completed.stdout and "FAIL" in completed.stdout
    assert "Architecture" in completed.stdout and "FAIL" in completed.stdout
    assert "Frontend unit" in completed.stdout and "PASS" in completed.stdout
    assert "make verify" in completed.stdout


def test_fast_check_lets_backend_and_frontend_operations_report_their_own_failures(
    tmp_path: Path,
) -> None:
    script = _isolated_runner(tmp_path)
    fake = tmp_path / "make"
    fake.write_text(
        "#!/bin/sh\n"
        "target=''\n"
        'for arg in "$@"; do case "$arg" in --*) ;; *) target="$arg" ;; esac; done\n'
        'case "$target" in test-python) exit 23 ;; frontend-typecheck) exit 41 ;; *) exit 0 ;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=script.parent.parent,
        env={**os.environ, "MAKE": str(fake)},
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 1
    assert "Backend dependencies" not in completed.stdout
    assert "Backend fast unit" in completed.stdout and "FAIL" in completed.stdout
    assert "Frontend typecheck" in completed.stdout and "FAIL" in completed.stdout
    assert "Frontend unit" in completed.stdout and "PASS" in completed.stdout
    source = SCRIPT.read_text(encoding="utf-8")
    assert "backend-dependencies-check" not in source
    assert "frontend-dependencies-check" not in source
