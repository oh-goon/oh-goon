from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _target(name: str) -> str:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    return text.split(f"{name}:", 1)[1].split("\n\n", 1)[0]


def test_recursive_make_output_hides_absolute_entering_directory_noise() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "MAKEFLAGS += --no-print-directory" in makefile


def test_step_timer_reports_duration_and_preserves_exit_code() -> None:
    success = subprocess.run(
        (
            "sh",
            "scripts/run_step.sh",
            "TEST STEP",
            "Owner: test",
            "--",
            "sh",
            "-c",
            "true",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert success.returncode == 0
    assert "Step completed in" in success.stdout

    failure = subprocess.run(
        (
            "sh",
            "scripts/run_step.sh",
            "TEST STEP",
            "Owner: test",
            "--",
            "sh",
            "-c",
            "exit 7",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert failure.returncode == 7
    assert "Step failed after" in failure.stderr


def test_step_timer_does_not_depend_on_external_date_executable(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "sh").symlink_to("/bin/sh")
    denied = tools / "date"
    denied.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    denied.chmod(0o644)
    env = dict(os.environ)
    env["PATH"] = str(tools)
    result = subprocess.run(
        (
            "/bin/sh",
            "scripts/run_step.sh",
            "TEST STEP",
            "Owner: test",
            "--",
            "/bin/sh",
            "-c",
            "exit 0",
        ),
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Permission denied" not in result.stdout + result.stderr
    assert "Step completed" in result.stdout


def test_init_progress_has_only_native_materialization_and_tiny_smoke() -> None:
    init = _target("init")
    assert init.count("$(RUN_STEP)") == 3
    assert "OH-GOON INIT [1/3]" in init
    assert "uv sync --project backend --locked" in init
    assert "OH-GOON INIT [2/3]" in init
    assert "npm ci --prefer-offline" in init
    assert "OH-GOON INIT [3/3]" in init
    assert "backend/.venv/bin/oh-goon --help" in init
    for forbidden in (
        "release-static-dir",
        "transport-clean",
        "backend-python-capability-check",
        "frontend_install.sh",
        "oh-goon-network-retry",
    ):
        assert forbidden not in init


def test_frontend_install_has_no_oh_goon_cache_owner() -> None:
    target = _target("frontend-install")
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in target
    assert not (ROOT / "scripts/frontend_install.sh").exists()


def test_no_separate_certification_dependency_setup_target_exists() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "certification-setup:" not in makefile
    assert "certification-workspace-setup:" not in makefile


def test_init_has_no_storage_admission_before_native_uv_and_doctor_is_separate() -> (
    None
):
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    init = _target("init")
    doctor = _target("init-doctor")
    assert "uv sync --project backend --locked" in init
    assert "INIT_DOCTOR" not in init
    assert "init_doctor.sh" not in init
    assert "release-static-dir" not in init
    assert "transport-clean" not in init
    assert "$(INIT_DOCTOR)" in doctor
    assert "DEPENDENCY_POLICY =" in makefile
    assert "$(DEPENDENCY_POLICY)" in (ROOT / "Makefile").read_text(encoding="utf-8")


@pytest.mark.parametrize("failure", ("", "backend", "cross-owner"))
def test_integration_gate_runs_both_owners_and_preserves_failures(
    tmp_path: Path, failure: str
) -> None:
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("run_step.sh", "command_banner.sh"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    executable = tmp_path / "backend/.venv/bin/oh-goon-qualification-exec"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "owner = 'backend' if Path.cwd().name == 'backend' else 'cross-owner'\n"
        "with Path(os.environ['OH_GOON_INTEGRATION_CALLS']).open('a') as stream:\n"
        "    stream.write(json.dumps({'owner': owner, 'argv': sys.argv[1:]}) + '\\n')\n"
        "sys.exit(7 if owner == os.environ['OH_GOON_INTEGRATION_FAILURE'] else 0)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    environment = dict(os.environ)
    environment["OH_GOON_INTEGRATION_CALLS"] = str(calls)
    environment["OH_GOON_INTEGRATION_FAILURE"] = failure
    result = subprocess.run(
        ("make", "test-integration"),
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    expected = [
        {
            "owner": "backend",
            "argv": ["pytest", "-m", "not certification", "tests/integration"],
        }
    ]
    if failure != "backend":
        expected.append(
            {
                "owner": "cross-owner",
                "argv": [
                    "pytest",
                    "-c",
                    "backend/pyproject.toml",
                    "-m",
                    "not certification",
                    "tests/integration",
                ],
            }
        )
    assert observed == expected
    assert result.returncode == (2 if failure else 0), result.stdout + result.stderr
    if failure:
        assert "exit 7" in result.stderr
