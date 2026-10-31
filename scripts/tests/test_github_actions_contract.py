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
    effect_indices = [
        index
        for index, command in enumerate(runs)
        if {"make", "scripts/development_test_resources.sh"}
        & set(shlex.split(command, comments=True))
    ]
    assert effect_indices and all(preparation_index < index for index in effect_indices)
    for scope in [job, *job["steps"]]:
        assert scope.get("env", {}).get("OH_GOON_TEST_CPUS") not in ("0", 0)
    for command in runs:
        assert "OH_GOON_TEST_CPUS=0" not in shlex.split(command, comments=True)
    return preparation


@pytest.mark.process
@pytest.mark.parametrize(
    "workflow_name",
    ["ci.yml", "release-validation.yml", "focused-feedback.yml"],
)
@pytest.mark.parametrize("manager_status", [0, 17])
def test_native_cpu_preparation_proves_user_bus_before_make_without_opting_out(
    tmp_path: Path, workflow_name: str, manager_status: int
) -> None:
    bash = shutil.which("bash", path=os.defpath)
    if bash is None:
        pytest.skip("native Bash is unavailable")
    workflow = yaml.safe_load((ROOT / ".github/workflows" / workflow_name).read_text())
    jobs = [
        job
        for job in workflow["jobs"].values()
        if any(
            {"loginctl", "systemctl"}
            <= set(shlex.split(step.get("run", ""), comments=True))
            for step in job.get("steps", [])
        )
    ]
    assert len(jobs) == 1
    preparation = _cpu_preparation(jobs[0])
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
    script = (
        (ROOT / "scripts/ci_native_runtime.sh")
        .read_text()
        .replace("/usr/local/lib/android/sdk", str(sdk))
    )
    script = "set -- make " + shlex.quote(target) + "\n" + script
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


@pytest.mark.parametrize("workflow_name", ["ci.yml", "focused-feedback.yml"])
def test_pull_request_checks_have_read_only_uncredentialed_execution(
    workflow_name: str,
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
    )
    assert workflow["permissions"] == {"contents": "read"}
    for job_name, job in workflow["jobs"].items():
        assert "permissions" not in job
        assert "secrets" not in job
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
        checkouts = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        if not checkouts:
            continue
        if "merge-gate" in job.get("name", ""):
            assert [step["with"]["ref"] for step in checkouts] == [
                job["env"]["BASE_SHA"],
                "${{ github.sha }}",
            ]
            assert len({step["with"]["path"] for step in checkouts}) == 2
        else:
            assert len(checkouts) == 1
            assert checkouts[0]["with"]["ref"] == "${{ github.sha }}"
        for checkout in checkouts:
            assert checkout["with"]["persist-credentials"] is False
            if job_name != "main-health":
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
    assert job["name"] != "merge-gate"
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


def test_explicit_ordinary_tooling_keeps_process_and_scale_coverage() -> None:
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


def _merge_aggregate() -> dict[str, Any]:
    aggregates = [
        job
        for job in _workflow()["jobs"].values()
        if "merge-gate" in job.get("name", "")
    ]
    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert isinstance(aggregate, dict)
    return aggregate


def test_relevant_events_keep_merge_candidates_and_main_health_distinct() -> None:
    workflow = _workflow()
    events = workflow["on"]
    assert "merge_group" in events and "pull_request" in events
    assert set(events["push"]) == {"branches"}
    assert events["push"]["branches"] == ["main"]
    assert "schedule" not in events and "pull_request_target" not in events
    assert "ready_for_review" in events["pull_request"]["types"]
    assert "edited" in events["pull_request"]["types"]
    assert "github.event_name" in workflow["concurrency"]["group"]
    assert "github.event.pull_request.number" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is True

    proof = workflow["jobs"]["verify"]
    assert proof["if"] == (
        "github.event_name != 'push' && "
        "(github.event.action != 'edited' || github.event.changes.base != null)"
    )
    assert "github.event.changes.base" in workflow["concurrency"]["group"]
    assert "'metadata' || 'candidate'" in workflow["concurrency"]["group"]
    assert "edit-not-applicable" in _merge_aggregate()["name"]
    assert "github.event.pull_request.base.sha" in proof["env"]["BASE_SHA"]
    assert "github.event.merge_group.base_sha" in proof["env"]["BASE_SHA"]
    assert "github.event.pull_request.head.sha" in proof["env"]["REQUESTED_HEAD"]
    assert "github.event.merge_group.head_sha" in proof["env"]["REQUESTED_HEAD"]
    assert workflow["jobs"]["main-health"]["if"] == "github.event_name == 'push'"


def test_fast_paths_have_no_hidden_complete_gate_or_unconditional_frontend_setup() -> (
    None
):
    workflow = _workflow()
    full_targets = {
        "backend-check",
        "tooling-check",
        "release-check",
        "build-release",
        "assemble-release",
        "test-integration",
        "test-python-projects",
        "frontend-check",
    }
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            words = shlex.split(step.get("run", ""), comments=True)
            assert not full_targets.intersection(words)
            assert words[:2] != ["make", "init"]
            if step.get("uses", "").startswith("actions/setup-node@") or words[:2] == [
                "make",
                "frontend-install",
            ]:
                assert step["if"] == "steps.selection.outputs.frontend == 'true'"
    for command in _make_commands(["check", "merge-check"]):
        assert not full_targets.intersection(command)


def test_selected_proof_prepares_the_original_native_python_build_environment() -> None:
    native = next(
        command
        for command in _make_commands(["backend-install"])
        if command[:2] == ["uv", "sync"]
    )
    steps = _workflow()["jobs"]["verify"]["steps"]
    preparations = [
        (index, step, shlex.split(step.get("run", "")))
        for index, step in enumerate(steps)
        if shlex.split(step.get("run", ""))[:2] == ["uv", "sync"]
    ]
    ((index, preparation, hosted),) = preparations
    native_groups = {
        native[position + 1]
        for position, value in enumerate(native)
        if value == "--group"
    }
    hosted_groups = {
        hosted[position + 1]
        for position, value in enumerate(hosted)
        if value == "--group"
    }
    assert "build" in native_groups and hosted_groups == native_groups
    assert (
        hosted[hosted.index("--project") + 1] == native[native.index("--project") + 1]
    )
    assert "--frozen" in hosted and "if" not in preparation
    assert index < next(
        position for position, step in enumerate(steps) if step.get("id") == "selection"
    )
    assert index < next(
        position
        for position, step in enumerate(steps)
        if "--execute" in shlex.split(step.get("run", ""))
    )


@pytest.mark.process
@pytest.mark.parametrize(
    ("mode", "admission_status"),
    [("focused", 0), ("merge", 0), ("merge", 2), ("merge", 17), ("merge", 124)],
)
def test_candidate_admission_passes_mode_and_stops_setup_after_rejection(
    tmp_path: Path, mode: str, admission_status: int
) -> None:
    bash = shutil.which("bash", path=os.defpath)
    git = shutil.which("git", path=os.defpath)
    assert bash is not None and git is not None
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    def native_git(*arguments: str) -> str:
        return subprocess.run(
            [git, "-C", str(checkout), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    native_git("init", "-q")
    readme = checkout / "README.md"
    for text in ("Original source.\n", "Candidate source.\n"):
        readme.write_text(text, encoding="utf-8")
        native_git("add", "README.md")
        native_git(
            "-c",
            "user.name=Verifier",
            "-c",
            "user.email=verifier@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "source fixture",
        )
    base = native_git("rev-parse", "HEAD^")
    candidate = native_git("rev-parse", "HEAD")
    scripts = checkout / "scripts"
    scripts.mkdir()
    (scripts / "development_test_resources.sh").write_text(
        '#!/bin/sh\nexec "$@"\n', encoding="utf-8"
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$ADMISSION_ARGUMENTS"\n'
        'exit "$ADMISSION_STATUS"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(mode=0o700)
    recorded, environment_file = tmp_path / "arguments", tmp_path / "github-env"
    post_admission = tmp_path / "post-admission-setup"
    proof = _workflow()["jobs"]["verify"]
    step = next(value for value in proof["steps"] if value.get("id") == "selection")
    assert "if" not in step and "continue-on-error" not in step
    # A later setup effect must not start after this real admission step fails.
    command = step["run"] + '\nprintf prepared > "$POST_ADMISSION_SETUP"\n'
    requested_head = "$(touch injected); `touch injected`"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
        cwd=checkout,
        env={
            "PATH": str(binaries) + os.pathsep + os.defpath,
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_ENV": str(environment_file),
            "GITHUB_OUTPUT": str(tmp_path / "github-output"),
            "BASE_SHA": base,
            "GITHUB_SHA": candidate,
            "REQUESTED_HEAD": requested_head,
            "VERIFY_MODE": mode,
            "ADMISSION_ARGUMENTS": str(recorded),
            "ADMISSION_STATUS": str(admission_status),
            "POST_ADMISSION_SETUP": str(post_admission),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == admission_status, result.stderr
    assert post_admission.exists() is (admission_status == 0)
    assert not (checkout / "injected").exists()
    arguments = recorded.read_bytes().decode().split("\0")[:-1]
    assert arguments[:7] == [
        "run",
        "--offline",
        "--frozen",
        "--no-sync",
        "--project",
        "backend",
        "oh-goon-verify-change",
    ]
    assert "--mode" in arguments, "admission must receive the requested proof mode"
    for flag, value in (
        ("--base", base),
        ("--candidate", candidate),
        ("--head", requested_head),
        ("--mode", mode),
    ):
        assert arguments[arguments.index(flag) + 1] == value
    control = Path(environment_file.read_text().strip().removeprefix("CONTROL_DIR="))
    assert control.parent == runner_temp and control.is_dir()
    assert arguments[arguments.index("--plan") + 1] == str(control / "plan.json")


@pytest.mark.process
@pytest.mark.parametrize(
    "result", ["success", "failure", "cancelled", "skipped", "", "neutral"]
)
def test_required_aggregate_rejects_every_non_success_dependency(
    tmp_path: Path, result: str
) -> None:
    aggregate = _merge_aggregate()
    assert "always()" in aggregate["if"]
    assert "needs." not in aggregate["if"]
    assert "continue-on-error" not in aggregate
    assert "draft-feedback" in aggregate["name"]
    guards = [
        (step, key)
        for step in aggregate["steps"]
        for key, value in step.get("env", {}).items()
        if isinstance(value, str)
        and re.fullmatch(r"\$\{\{ needs\.\w+\.result \}\}", value)
    ]
    assert len(guards) == 1
    step, key = guards[0]
    assert "continue-on-error" not in step and "if" not in step
    bash = shutil.which("bash", path=os.defpath)
    assert bash is not None
    observed = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={"PATH": os.defpath, key: result},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert observed.returncode == (0 if result == "success" else 1), observed.stderr


@pytest.mark.process
@pytest.mark.parametrize("exit_code", [0, 17, 124])
def test_merge_control_uses_only_the_trusted_base_and_keeps_its_failure(
    tmp_path: Path, exit_code: int
) -> None:
    job = _merge_aggregate()
    steps = [step for step in job["steps"] if "--trusted-root" in step.get("run", "")]
    assert len(steps) == 1
    step = steps[0]
    assert "continue-on-error" not in step
    assert step["if"] == "github.event.pull_request.draft != true"
    assert "${{" not in step["run"]
    checkouts = [
        value["with"]
        for value in job["steps"]
        if value.get("uses", "").startswith("actions/checkout@")
    ]
    trusted, candidate = [value["path"] for value in checkouts]
    binaries = tmp_path / "bin"
    binaries.mkdir()
    recorded = tmp_path / "arguments"
    uv = binaries / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$RECORDED"\nexit "$CONTROL_STATUS"\n'
    )
    uv.chmod(0o755)
    bash = shutil.which("bash", path=os.defpath)
    assert bash is not None
    head = "a" * 40
    # Evidence fields are data even when an invalid field contains shell syntax.
    base = "$(touch injected); `touch injected`"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={
            "PATH": str(binaries) + os.pathsep + os.defpath,
            "RECORDED": str(recorded),
            "CONTROL_STATUS": str(exit_code),
            "GITHUB_SHA": head,
            "BASE_SHA": base,
            "REQUESTED_HEAD": head,
            "GITHUB_WORKSPACE": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    arguments = recorded.read_bytes().decode().split("\0")[:-1]
    assert arguments[:8] == [
        "run",
        "--offline",
        "--frozen",
        "--no-sync",
        "--project",
        f"{trusted}/backend",
        "python",
        "-I",
    ]
    assert (
        arguments[8]
        == f"{trusted}/backend/src/oh_goon/repository_tooling/verification_gate_control.py"
    )
    for flag, value in (
        ("--root", str(tmp_path / candidate)),
        ("--trusted-root", str(tmp_path / trusted)),
        ("--base", base),
        ("--control-base", base),
        ("--candidate", head),
        ("--head", head),
    ):
        assert arguments[arguments.index(flag) + 1] == value
    assert not (tmp_path / "injected").exists()
