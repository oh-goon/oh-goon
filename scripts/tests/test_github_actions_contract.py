from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from oh_goon.repository_tooling.pytest_batch_cli import parse_batch_args

ROOT = Path(__file__).resolve().parents[2]


def _workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    return workflow


def _cpu_preparation(job: dict[str, Any]) -> str:
    runs = [
        step["run"]
        for step in job["steps"]
        if "run" in step and step.get("if") != "always()"
    ]
    preparations = [
        (index, command)
        for index, command in enumerate(runs)
        if {"loginctl", "systemctl"} <= set(shlex.split(command, comments=True))
    ]
    ((preparation_index, preparation),) = preparations
    assert isinstance(preparation, str)
    make_indices = [
        index
        for index, command in enumerate(runs)
        if "make" in shlex.split(command, comments=True)
    ]
    assert make_indices and all(preparation_index < index for index in make_indices)
    for scope in [job, *job["steps"]]:
        assert scope.get("env", {}).get("OH_GOON_TEST_CPUS") not in ("0", 0)
    for command in runs:
        assert "OH_GOON_TEST_CPUS=0" not in shlex.split(command, comments=True)
    return preparation


def test_required_checks_run_for_ready_pull_requests_main_and_merge_groups() -> None:
    workflow = _workflow()
    assert workflow["on"] == {
        "pull_request": {
            "types": [
                "opened",
                "synchronize",
                "reopened",
                "ready_for_review",
                "converted_to_draft",
            ]
        },
        "push": {"branches": ["main"]},
        "merge_group": None,
    }
    assert set(workflow["jobs"]) == {"checks", "draft-feedback", "release"}
    checks = workflow["jobs"]["checks"]
    assert checks["name"] == (
        "${{ github.event_name == 'pull_request' && github.event.pull_request.draft "
        "&& 'full-checks-pending-review' || matrix.target }}"
    )
    assert checks["if"] == (
        "github.event_name != 'pull_request' || github.event.pull_request.draft == false"
    )
    assert "matrix" not in checks["if"]
    assert checks["strategy"]["fail-fast"] is False
    assert set(checks["strategy"]["matrix"]["target"]) == {
        "backend-check",
        "frontend-check",
        "tooling-check",
        "architecture",
        "check-generated",
        "test-integration",
    }
    assert "continue-on-error" not in checks
    for step in checks["steps"]:
        assert "if" not in step
        assert "continue-on-error" not in step
    commands = [step["run"] for step in checks["steps"] if "run" in step]
    assert "make init" in commands
    _cpu_preparation(checks)


@pytest.mark.process
@pytest.mark.parametrize(
    "job_name",
    ["draft-feedback", "checks", "release", "complete-release", "focused-behavior"],
)
@pytest.mark.parametrize("manager_status", [0, 17])
def test_native_cpu_preparation_proves_user_bus_before_make_without_opting_out(
    tmp_path: Path, job_name: str, manager_status: int
) -> None:
    bash = shutil.which("bash", path=os.defpath)
    if bash is None:
        pytest.skip("native Bash is unavailable")
    if job_name == "focused-behavior":
        job = _focused_workflow()["jobs"][job_name]
    elif job_name == "complete-release":
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-validation.yml").read_text()
        )
        job = workflow["jobs"]["full-regression"]
    else:
        job = _workflow()["jobs"][job_name]
    preparation = _cpu_preparation(job)
    commands = tmp_path / "commands"
    commands.mkdir()
    events = tmp_path / "events"
    exported = tmp_path / "github-env"
    for name in ("id", "sudo", "make"):
        tool = commands / name
        tool.write_text(
            f"#!{bash}\n"
            'printf "%s:%s\\n" "${0##*/}" "$*" >> "$PREPARATION_EVENTS"\n'
            'case "${0##*/}" in\n'
            '  id) test "$*" = -u; printf "1117\\n" ;;\n'
            '  sudo) if test "$1" = systemctl; then exit "$MANAGER_STATUS"; fi ;;\n'
            '  make) test "$*" = init; test "${OH_GOON_TEST_CPUS-unset}" != 0 ;;\n'
            "  *) exit 99 ;;\n"
            "esac\n"
        )
        tool.chmod(0o755)
    with (
        tempfile.TemporaryDirectory(prefix="og-ci-bus-") as runtime,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bus,
    ):
        bus_path = Path(runtime) / "1117/bus"
        bus_path.parent.mkdir()
        bus.bind(str(bus_path))
        script = preparation.replace("/run/user/", runtime + "/") + "\nmake init\n"
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=tmp_path,
            env={
                "PATH": str(commands) + os.pathsep + os.defpath,
                "RUNNER_ENVIRONMENT": "github-hosted",
                "GITHUB_ENV": str(exported),
                "PREPARATION_EVENTS": str(events),
                "MANAGER_STATUS": str(manager_status),
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == manager_status, result.stderr
        expected = [
            "id:-u",
            "sudo:loginctl enable-linger 1117",
            "sudo:systemctl start user@1117.service",
        ]
        if manager_status == 0:
            expected.append("make:init")
            environment = dict(
                line.split("=", 1) for line in exported.read_text().splitlines()
            )
            assert environment == {
                "XDG_RUNTIME_DIR": str(bus_path.parent),
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus_path}",
            }
            assert "OH_GOON_TEST_CPUS" not in environment
        else:
            assert not exported.exists()
        assert events.read_text().splitlines() == expected


_SDK_SHIM = r"""set -euo pipefail
case "${0##*/}" in
  sudo)
    case "$1" in
      mount)
        echo mount >> "$SDK_EVENTS"
        if [ "${SIM_MOUNT_STATUS:-0}" = 0 ] || [ "${SIM_PARTIAL_MOUNT:-0}" = 1 ]; then
          printf '%s\n' "${@: -2:1}" > "$SDK_MARKER"
        fi
        exit "${SIM_MOUNT_STATUS:-0}"
        ;;
      umount)
        echo umount >> "$SDK_EVENTS"
        if [ "${SIM_CLEANUP_STATUS:-0}" = 0 ]; then /bin/rm -f -- "$SDK_MARKER"; fi
        exit "${SIM_CLEANUP_STATUS:-0}"
        ;;
      *) exit 99 ;;
    esac
    ;;
  mountpoint)
    if [ "${SIM_PREEXISTING_MOUNT:-0}" = 1 ]; then exit 0; else exit 1; fi
    ;;
  findmnt)
    requested_source= columns=
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -S) shift; requested_source=$1 ;;
        -o|--output) shift; columns=$1 ;;
      esac
      shift
    done
    if [ -n "$requested_source" ]; then
      if [ "${SIM_LOST_OWNER:-0}" = 0 ] && [ -f "$SDK_MARKER" ] &&
          [ "$(< "$SDK_MARKER")" = "$requested_source" ]; then exit 0; fi
      exit 1
    fi
    if [ "$columns" = SOURCE ]; then
      if [ "${SIM_FOREIGN_TOP:-0}" = 1 ]; then
        echo foreign-top-source
      elif [ "${SIM_LOST_OWNER:-0}" = 1 ] || [ ! -f "$SDK_MARKER" ]; then
        echo parent-filesystem-source
      else
        printf '%s\n' "$(< "$SDK_MARKER")"
      fi
      exit 0
    fi
    echo 'owned-sdk tmpfs ro,nodev,nosuid,noexec'
    ;;
  bwrap)
    echo view >> "$SDK_EVENTS"
    exit "${SIM_VIEW_STATUS:-0}"
    ;;
  make)
    printf 'make:%s\n' "$*" >> "$SDK_EVENTS"
    if [ "${SIM_TERM:-0}" = 1 ]; then kill -TERM "$SDK_WORKFLOW_PID"; fi
    if [ "$1" = test-python-projects ]; then exit "${SIM_PROJECT_CHECK_STATUS:-0}"; fi
    exit "${SIM_CHECK_STATUS:-0}"
    ;;
  *) exit 99 ;;
esac
"""


def _run_required_check(
    tmp_path: Path, target: str, overrides: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str], bool]:
    """Execute native Bash while every privileged/tool effect is simulated."""
    bash = shutil.which("bash", path=os.defpath)
    if bash is None:
        pytest.skip("native Bash is unavailable")
    options = overrides or {}
    sdk = tmp_path / "sdk"
    if options.get("SIM_SDK_PRESENT", "1") == "1":
        if options.get("SIM_SDK_KIND") == "file":
            sdk.write_text("unrelated host file")
        elif options.get("SIM_SDK_KIND") == "symlink":
            target_directory = tmp_path / "unrelated-host-directory"
            target_directory.mkdir()
            sdk.symlink_to(target_directory, target_is_directory=True)
        else:
            sdk.mkdir()
    commands = tmp_path / "commands"
    commands.mkdir()
    for command in ("sudo", "mountpoint", "findmnt", "bwrap", "make"):
        executable = commands / command
        executable.write_text(f"#!{bash}\n" + _SDK_SHIM)
        executable.chmod(0o755)
    events, marker = tmp_path / "events", tmp_path / "mounted-owner"
    steps = _workflow()["jobs"]["checks"]["steps"]
    script = [step["run"] for step in steps if "run" in step][-1]
    script = script.replace("${{ matrix.target }}", target).replace(
        "/usr/local/lib/android/sdk", str(sdk)
    )
    # Keep cancellation bound to our child even if a missing trap lets it exec Make.
    script = "export SDK_WORKFLOW_PID=$$\n" + script
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": str(commands) + os.pathsep + os.defpath,
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_RUN_ID": "synthetic-owned-run",
            "SDK_EVENTS": str(events),
            "SDK_MARKER": str(marker),
            **options,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return (
        result,
        events.read_text().splitlines() if events.exists() else [],
        marker.exists(),
    )


@pytest.mark.process
@pytest.mark.parametrize(
    "target",
    [
        "backend-check",
        "frontend-check",
        "tooling-check",
        "architecture",
        "check-generated",
        "test-integration",
    ],
)
@pytest.mark.parametrize("check_status", [0, 17])
def test_required_check_executes_exact_matrix_target_and_preserves_its_status(
    tmp_path: Path, target: str, check_status: int
) -> None:
    result, events, mounted = _run_required_check(
        tmp_path, target, {"SIM_CHECK_STATUS": str(check_status)}
    )
    assert result.returncode == check_status, result.stderr
    expected = [f"make:{target}"]
    if target == "test-integration" and check_status == 0:
        expected.append("make:test-python-projects")
    if target in {"backend-check", "test-integration"}:
        expected = ["mount", "view", *expected, "umount"]
    assert events == expected
    assert not mounted


@pytest.mark.process
def test_required_integration_project_failure_preserves_status_and_releases_sdk_overlay(
    tmp_path: Path,
) -> None:
    result, events, mounted = _run_required_check(
        tmp_path, "test-integration", {"SIM_PROJECT_CHECK_STATUS": "23"}
    )
    assert result.returncode == 23, result.stderr
    assert events == [
        "mount",
        "view",
        "make:test-integration",
        "make:test-python-projects",
        "umount",
    ]
    assert not mounted


@pytest.mark.process
@pytest.mark.parametrize(
    "options,expected_status,expected_events,retained_mount",
    [
        ({"RUNNER_ENVIRONMENT": "self-hosted"}, 1, [], False),
        ({"SIM_PREEXISTING_MOUNT": "1"}, 1, [], False),
        ({"SIM_SDK_KIND": "symlink"}, 1, [], False),
        ({"SIM_SDK_KIND": "file"}, 1, [], False),
        ({}, 0, ["mount", "view", "make:backend-check", "umount"], False),
        (
            {"SIM_CHECK_STATUS": "17"},
            17,
            ["mount", "view", "make:backend-check", "umount"],
            False,
        ),
        (
            {"SIM_CLEANUP_STATUS": "1"},
            1,
            ["mount", "view", "make:backend-check", "umount"],
            True,
        ),
        (
            {"SIM_CHECK_STATUS": "17", "SIM_CLEANUP_STATUS": "1"},
            17,
            ["mount", "view", "make:backend-check", "umount"],
            True,
        ),
        ({"SIM_MOUNT_STATUS": "9"}, 9, ["mount"], False),
        (
            {"SIM_MOUNT_STATUS": "9", "SIM_PARTIAL_MOUNT": "1"},
            9,
            ["mount", "umount"],
            False,
        ),
        ({"SIM_VIEW_STATUS": "11"}, 11, ["mount", "view", "umount"], False),
        (
            {"SIM_LOST_OWNER": "1"},
            1,
            ["mount", "view", "make:backend-check"],
            True,
        ),
        (
            {"SIM_FOREIGN_TOP": "1"},
            1,
            ["mount", "view", "make:backend-check"],
            True,
        ),
        ({"SIM_SDK_PRESENT": "0"}, 0, ["make:backend-check"], False),
        (
            {"SIM_TERM": "1"},
            143,
            ["mount", "view", "make:backend-check", "umount"],
            False,
        ),
    ],
    ids=[
        "non-hosted",
        "preexisting-mount",
        "symlink-target",
        "regular-file-target",
        "success",
        "check-failure",
        "cleanup-failure",
        "both-fail",
        "mount-failure",
        "partial-mount-failure",
        "view-failure",
        "missing-owner",
        "foreign-top-mount",
        "sdk-absent",
        "term",
    ],
)
def test_sdk_overlay_lifecycle_preserves_outcome_and_cleans_only_owned_mounts(
    tmp_path: Path,
    options: dict[str, str],
    expected_status: int,
    expected_events: list[str],
    retained_mount: bool,
) -> None:
    result, events, mounted = _run_required_check(tmp_path, "backend-check", options)
    assert result.returncode == expected_status, result.stderr
    assert events == expected_events
    assert mounted is retained_mount


def test_release_assembles_only_after_all_six_checks_for_the_same_source() -> None:
    jobs = _workflow()["jobs"]
    checks, release = jobs["checks"], jobs["release"]
    assert release["needs"] == "checks"
    assert release["if"] == checks["if"]
    assert release["name"] == (
        "${{ github.event_name == 'pull_request' && github.event.pull_request.draft "
        "&& 'release-pending-review' || 'build-release' }}"
    )
    assert "strategy" not in release
    assert "continue-on-error" not in release
    assert all(
        "if" not in step and "continue-on-error" not in step
        for step in release["steps"]
    )
    preparation = _cpu_preparation(release)
    assert [
        step["run"]
        for step in release["steps"]
        if "run" in step and step["run"] != preparation
    ] == [
        "make init",
        "make assemble-release",
    ]
    assert set(checks["strategy"]["matrix"]["target"]) | {"build-release"} == {
        "backend-check",
        "frontend-check",
        "tooling-check",
        "architecture",
        "check-generated",
        "test-integration",
        "build-release",
    }
    # Native GitHub needs/checkout authority replaces no source or test resolver.
    assert "always()" not in release["if"]
    assert not any(
        "download-artifact" in step.get("uses", "") for step in release["steps"]
    )


@pytest.mark.parametrize("workflow_name", ["ci.yml", "focused-feedback.yml"])
def test_pull_request_checks_have_read_only_uncredentialed_execution(
    workflow_name: str,
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
    )
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert "permissions" not in job
        assert "secrets" not in job
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
        checkout = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout["with"]["ref"] == "${{ github.sha }}"
        assert checkout["with"]["persist-credentials"] is False
        assert checkout["with"]["fetch-depth"] == 0


def _make_commands(
    targets: list[str], roots: str | None = None, *, workers: int | None = None
) -> list[list[str]]:
    environment = os.environ.copy()
    for name in (
        "BACKEND_CHECK_TEST_ROOTS",
        "BACKEND_CHECK_TEST_WORKERS",
        "INTEGRATION_TEST_WORKERS",
        "CHECK_PYTEST_MARKER",
        "TOOLING_TEST_MARKER",
        "TOOLING_COMPLETE_TEST_MARKER",
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
    ):
        environment.pop(name, None)
    if roots is not None:
        environment["BACKEND_CHECK_TEST_ROOTS"] = roots
    if workers is not None:
        environment["BACKEND_CHECK_TEST_WORKERS"] = str(workers)
        environment["INTEGRATION_TEST_WORKERS"] = str(workers)
    result = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", *targets],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    commands = []
    for line in result.stdout.replace("\\\n", " ").splitlines():
        command = shlex.split(line)
        commands.append(command)
    return commands


def _batch_invocations(
    commands: list[list[str]],
) -> list[tuple[Path, argparse.Namespace]]:
    invocations = []
    for command in commands:
        runner = next(
            (
                index
                for index, word in enumerate(command)
                if word.endswith("/oh-goon-test-batches")
            ),
            None,
        )
        if runner is None:
            continue
        assert command[:2] == ["bash", "scripts/development_test_resources.sh"]
        cwd = Path(".")
        for index in range(runner):
            if command[index : index + 2] == ["sh", "-c"]:
                assert shlex.split(command[index + 2]) == [
                    "cd",
                    "backend",
                    "&&",
                    "exec",
                    "$@",
                ]
                cwd = Path("backend")
        with patch.object(sys, "argv", command[runner:]):
            _parser, arguments = parse_batch_args()
        invocations.append((cwd, arguments))
    return invocations


def test_draft_feedback_keeps_separate_names_and_adds_bounded_focused_behavior() -> (
    None
):
    jobs = _workflow()["jobs"]
    draft = jobs["draft-feedback"]
    required_names = [*jobs["checks"]["strategy"]["matrix"]["target"], "build-release"]
    assert draft["name"] not in required_names
    assert "full-checks-pending-review" not in required_names
    assert draft["if"] == (
        "github.event_name == 'pull_request' && github.event.pull_request.draft == true"
    )
    assert "strategy" not in draft and "continue-on-error" not in draft
    assert all("continue-on-error" not in step for step in draft["steps"])
    summary = draft["steps"][-1]
    assert summary["if"] == "always()"
    assert "NOT_FULLY_VERIFIED" in summary["run"]
    assert all("if" not in step for step in draft["steps"][:-1])
    commands = [step["run"] for step in draft["steps"] if "run" in step]
    preparation = _cpu_preparation(draft)
    assert [command for command in commands if command != preparation][:2] == [
        "make init",
        "make lint format-check architecture release-locks-check",
    ]
    native_commands = _make_commands(
        ["init", "lint", "format-check", "architecture", "release-locks-check"]
    )
    assert not any(
        "pytest" in command
        or any(word.endswith("/oh-goon-test-batches") for word in command)
        for command in native_commands
    )
    smoke = draft["steps"][-2]["run"]
    command = shlex.split(smoke.replace("\\\n", " "))
    assert command[:3] == ["bash", "scripts/development_test_resources.sh", "uv"]
    runner = command.index("oh-goon-test-batches")
    with patch.object(sys, "argv", command[runner:]):
        _parser, options = parse_batch_args()
    assert options.focused and options.qualified and options.show_output
    assert options.next_check == "make backend-check"
    assert options.workers == 1 and options.batch_size == 64
    assert options.batch_timeout == 120 and options.suite_timeout == 300
    assert options.marker == "not certification and not slow"
    assert not options.resume and options.batch is None
    assert len(options.paths) == 3
    assert all((ROOT / path).is_file() for path in options.paths)


def _focused_workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/focused-feedback.yml").read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    return workflow


def test_manual_focused_run_never_publishes_required_checks_or_cancels_full_ci() -> (
    None
):
    workflow = _focused_workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"test_selection", "next_check"}
    assert all(
        value["required"] and value["type"] == "string" for value in inputs.values()
    )
    assert workflow["concurrency"]["group"].startswith("${{ github.workflow }}-")
    assert set(workflow["jobs"]) == {"focused-behavior"}
    job = workflow["jobs"]["focused-behavior"]
    required = _workflow()["jobs"]["checks"]["strategy"]["matrix"]["target"]
    assert job["name"] not in [*required, "build-release"]
    assert "continue-on-error" not in job and job["timeout-minutes"] == 15
    assert job["steps"][-1]["if"] == "always()"
    assert "NOT_FULLY_VERIFIED" in job["steps"][-1]["run"]
    for step in job["steps"][:-1]:
        assert "if" not in step and "continue-on-error" not in step
    _cpu_preparation(job)


@pytest.mark.process
@pytest.mark.parametrize("exit_code", [0, 17, 124, 130])
def test_manual_focused_selection_and_guidance_are_inert_argv_and_preserve_failure(
    tmp_path: Path, exit_code: int
) -> None:
    bash = shutil.which("bash", path=os.defpath)
    assert bash is not None
    commands = tmp_path / "commands"
    commands.mkdir()
    forwarded = tmp_path / "argv"
    shim = commands / "bash"
    shim.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$FORWARDED"\nexit "$TEST_STATUS"\n'
    )
    shim.chmod(0o755)
    selection = "backend/tests/unit/core/test_x.py::test_case[$(touch injected); `touch injected`]"
    guidance = "make backend-check\n$(touch injected); `touch injected`"
    step = _focused_workflow()["jobs"]["focused-behavior"]["steps"][-2]
    assert step["env"] == {
        "TEST_SELECTION": "${{ inputs.test_selection }}",
        "NEXT_CHECK": "${{ inputs.next_check }}",
    }
    assert "${{" not in step["run"]
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={
            "PATH": str(commands) + os.pathsep + os.defpath,
            "TEST_SELECTION": selection,
            "NEXT_CHECK": guidance,
            "FORWARDED": str(forwarded),
            "TEST_STATUS": str(exit_code),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    arguments = forwarded.read_bytes().decode().split("\0")[:-1]
    assert arguments[-2:] == ["--", selection]
    assert arguments[arguments.index("--next-check") + 1] == guidance
    assert not (tmp_path / "injected").exists()
    runner = arguments.index("oh-goon-test-batches")
    with patch.object(sys, "argv", arguments[runner:]):
        _parser, options = parse_batch_args()
    assert options.paths == [Path(selection)]
    assert options.focused and options.qualified and options.workers == 1
    assert options.suite_timeout == 600 and options.batch_timeout == 180
    assert options.marker == "not certification and not slow"


@pytest.mark.process
@pytest.mark.parametrize("missing_input", ["TEST_SELECTION", "NEXT_CHECK"])
def test_manual_focused_run_refuses_empty_input_before_launch(
    tmp_path: Path, missing_input: str
) -> None:
    bash = shutil.which("bash", path=os.defpath)
    assert bash is not None
    # No child executable is available: success here would silently admit a broad run.
    environment = {
        "PATH": str(tmp_path),
        "TEST_SELECTION": "backend/tests/unit/core/test_error_kinds.py",
        "NEXT_CHECK": "make backend-check",
    }
    environment[missing_input] = ""
    step = _focused_workflow()["jobs"]["focused-behavior"]["steps"][-2]
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stderr == ""


def test_hosted_backend_roots_partition_the_complete_suite_without_nested_selection() -> (
    None
):
    checks = _workflow()["jobs"]["checks"]
    selection = {row["target"]: row for row in checks["strategy"]["matrix"]["include"]}
    assert set(selection) == {"backend-check", "test-integration"}
    roots = selection["backend-check"]["backend_test_roots"]
    assert checks["env"]["BACKEND_CHECK_TEST_ROOTS"] == (
        "${{ matrix.backend_test_roots || '' }}"
    )
    assert "--ignore" not in checks["env"]["PYTEST_ADDOPTS"]
    assert not any(root in checks["env"]["PYTEST_ADDOPTS"] for root in roots.split())

    # Without an explicit hosted partition, pytest retains its native testpaths.
    ((default_cwd, default),) = _batch_invocations(_make_commands(["backend-check"]))
    assert default_cwd == Path("backend")
    assert default.paths == []
    ((backend_cwd, backend),) = _batch_invocations(
        _make_commands(["backend-check"], roots)
    )
    assert backend_cwd == Path("backend")
    assert backend.paths == [Path(path) for path in roots.split()]
    integration = _batch_invocations(_make_commands(["test-integration"]))
    assert [cwd for cwd, _options in integration] == [Path("backend"), Path(".")]
    assert [options.paths for _cwd, options in integration] == [
        [Path("tests/integration")],
        [Path("tests/integration")],
    ]
    options = [default, backend, *(arguments for _cwd, arguments in integration)]
    assert [option.pytest_config for option in options] == [
        Path("pyproject.toml"),
        Path("pyproject.toml"),
        Path("pyproject.toml"),
        Path("backend/pyproject.toml"),
    ]
    assert all(
        option.qualified and option.show_output and option.workers == 1
        for option in options
    )
    assert all(option.marker == "not certification and not slow" for option in options)
    assert all(
        option.granularity == "tests" and option.batch_timeout == 180
        for option in options
    )
    assert [option.batch_size for option in options] == [64, 64, 8, 8]
    assert all(option.pytest_arg == [] and option.exclude == [] for option in options)
    assert all(option.batch is None and not option.resume for option in options)
    pytest_config = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
    testpaths = pytest_config["tool"]["pytest"]["ini_options"]["testpaths"]
    admitted_roots = [str(path) for path in (*backend.paths, *integration[0][1].paths)]
    assert sorted(admitted_roots) == sorted(testpaths)
    assert len(admitted_roots) == len(set(admitted_roots))

    # Hosted parallelism uses the original runner and selection, within the same
    # native two-CPU budget; local defaults and release selection remain serial.
    assert selection["backend-check"]["backend_test_workers"] == 2
    assert selection["test-integration"]["integration_test_workers"] == 2
    assert checks["env"]["BACKEND_CHECK_TEST_WORKERS"] == (
        "${{ matrix.backend_test_workers || 1 }}"
    )
    assert checks["env"]["INTEGRATION_TEST_WORKERS"] == (
        "${{ matrix.integration_test_workers || 1 }}"
    )
    hosted = _batch_invocations(
        _make_commands(["backend-check", "test-integration"], roots, workers=2)
    )
    assert [cwd for cwd, _arguments in hosted] == [
        Path("backend"),
        Path("backend"),
        Path("."),
    ]
    for (_cwd, parallel), serial in zip(
        hosted, [backend, *[a for _c, a in integration]], strict=True
    ):
        assert parallel.workers == 2
        assert vars(parallel) | {"workers": 1} == vars(serial)


def test_routine_tooling_keeps_process_and_scale_coverage_while_deferring_exhaustive_cases() -> (
    None
):
    commands = _make_commands(["tooling-check"])
    batches = [
        command
        for command in commands
        if any(word.endswith("/oh-goon-test-batches") for word in command)
    ]
    assert len(batches) == 1
    assert (
        batches[0][batches[0].index("--marker") + 1] == "not certification and not slow"
    )


def test_complete_release_check_executes_each_native_surface_once() -> None:
    commands = _make_commands(["release-check"])
    invocations = _batch_invocations(commands)
    assert [(cwd, arguments.paths) for cwd, arguments in invocations] == [
        (Path("backend"), [Path("tests/unit"), Path("tests/dependency_complete")]),
        (Path("."), [Path("scripts/tests")]),
        (Path("backend"), [Path("tests/integration")]),
        (Path("."), [Path("tests/integration")]),
    ]
    backend, tooling, backend_integration, cross_owner = [
        options for _cwd, options in invocations
    ]
    assert all(options.marker == "not certification" for _cwd, options in invocations)
    assert all(
        options.batch is None and not options.resume for _cwd, options in invocations
    )
    assert tooling.qualified is False and tooling.workers == 2
    assert tooling.pytest_config == Path("backend/pyproject.toml")
    assert tooling.granularity == "files" and tooling.batch_size == 4
    for options, configuration, batch_size in (
        (backend, Path("pyproject.toml"), 64),
        (backend_integration, Path("pyproject.toml"), 8),
        (cross_owner, Path("backend/pyproject.toml"), 8),
    ):
        assert options.qualified and options.show_output
        assert options.workers == 1 and options.pytest_config == configuration
        assert options.pytest_arg == [] and options.exclude == []
        assert options.granularity == "tests"
        assert options.batch_size == batch_size and options.batch_timeout == 180
    assert not any("pytest" in command for command in commands)
    assert sum(command[-3:] == ["npm", "run", "check"] for command in commands) == 1


def test_complete_release_verification_is_explicit_and_uses_the_selected_source() -> (
    None
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/release-validation.yml").read_text()
    )
    assert workflow["on"] == {
        "workflow_dispatch": None,
        "release": {"types": ["published"]},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert len(workflow["jobs"]) == 1
    job = next(iter(workflow["jobs"].values()))
    assert "continue-on-error" not in job
    assert all(
        "continue-on-error" not in step and "if" not in step for step in job["steps"]
    )
    checkout = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] is False
    _cpu_preparation(job)
    assert [step["run"] for step in job["steps"] if "run" in step][-2:] == [
        "make init",
        "make release-check",
    ]
