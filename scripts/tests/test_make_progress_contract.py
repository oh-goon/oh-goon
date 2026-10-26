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


@pytest.mark.parametrize("mypy_exit", (0, 17))
def test_backend_gate_checks_configured_package_types_before_running_tests(
    tmp_path: Path, mypy_exit: int
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    backend = tmp_path / "backend"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(
        ROOT / "scripts/development_test_resources.sh",
        scripts / "development_test_resources.sh",
    )
    executor = backend / ".venv/bin/oh-goon-test-batches"
    executor.parent.mkdir(parents=True)
    recorder = (
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['OH_GOON_TEST_BACKEND_CALLS']).open('a') as stream:\n"
        "    stream.write(json.dumps({'tool': Path(sys.argv[0]).name, 'argv': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')\n"
        "sys.exit(int(os.environ['OH_GOON_TEST_MYPY_EXIT']) if 'mypy' in sys.argv[1:] else 0)\n"
    )
    for executable in (tools / "uv", executor):
        executable.write_text(recorder, encoding="utf-8")
        executable.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    environment = dict(os.environ)
    for name in (
        "BACKEND_CHECK_TEST_ROOTS",
        "CHECK_PYTEST_MARKER",
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
    ):
        environment.pop(name, None)
    environment.update(
        PATH=f"{tools}{os.pathsep}{environment.get('PATH', os.defpath)}",
        OH_GOON_TEST_BACKEND_CALLS=str(calls),
        OH_GOON_TEST_MYPY_EXIT=str(mypy_exit),
        OH_GOON_TEST_CPUS="0",
    )
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(ROOT / "Makefile"), "backend-check"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    typed = [event for event in observed if "mypy" in event["argv"]]
    tests = [event for event in observed if event["tool"] == "oh-goon-test-batches"]
    assert len(typed) == 1
    assert typed[0]["cwd"] == str(backend)
    assert typed[0]["argv"][-1] == "mypy"  # Use native configured package selection.
    assert result.returncode == (2 if mypy_exit else 0), result.stdout + result.stderr
    if mypy_exit:
        assert tests == []
        assert "17" in result.stderr
    else:
        assert len(tests) == 1
        assert all(observed.index(typed[0]) < observed.index(test) for test in tests)


@pytest.mark.parametrize("failure", ("", "backend", "cross-owner"))
@pytest.mark.parametrize(
    ("marker_override", "expected_marker"),
    (
        (None, "not certification and not slow"),
        ("not certification", "not certification"),
    ),
)
def test_integration_gate_runs_both_owners_and_preserves_failures(
    tmp_path: Path, failure: str, marker_override: str | None, expected_marker: str
) -> None:
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("run_step.sh", "command_banner.sh", "development_test_resources.sh"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    executable = tmp_path / "backend/.venv/bin/oh-goon-test-batches"
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
    for name in ("CHECK_PYTEST_MARKER", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
        environment.pop(name, None)
    environment["OH_GOON_INTEGRATION_CALLS"] = str(calls)
    environment["OH_GOON_INTEGRATION_FAILURE"] = failure
    environment["OH_GOON_TEST_CPUS"] = "0"
    command = ["make", "test-integration"]
    if marker_override is not None:
        command.append(f"CHECK_PYTEST_MARKER={marker_override}")
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    expected_owners = ["backend"]
    expected_markers = [expected_marker]
    expected_configs = ["pyproject.toml"]
    if failure != "backend":
        expected_owners.append("cross-owner")
        expected_markers.append(expected_marker)
        expected_configs.append("backend/pyproject.toml")
    assert [event["owner"] for event in observed] == expected_owners
    for event, marker, config in zip(
        observed, expected_markers, expected_configs, strict=True
    ):
        arguments = event["argv"]
        assert "--qualified" in arguments
        assert arguments[arguments.index("--marker") + 1] == marker
        if config == "pyproject.toml":
            assert arguments[arguments.index("--pytest-config") + 1] == config
        else:
            assert "--pytest-config" not in arguments
        assert arguments.count("tests/integration") == 1
        assert arguments[arguments.index("--workers") + 1] == "1"
        assert arguments[arguments.index("--batch-size") + 1] == "4"
    assert result.returncode == (2 if failure else 0), result.stdout + result.stderr
    if failure:
        assert "exit 7" in result.stderr
