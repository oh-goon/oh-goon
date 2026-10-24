from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    return workflow


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
    if target in {"backend-check", "test-integration"}:
        expected = ["mount", "view", *expected, "umount"]
    assert events == expected
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
    assert [step["run"] for step in release["steps"] if "run" in step] == [
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


def test_pull_request_checks_have_read_only_uncredentialed_execution() -> None:
    workflow = _workflow()
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


def _make_commands(targets: list[str], roots: str | None = None) -> list[list[str]]:
    environment = os.environ.copy()
    for name in (
        "BACKEND_CHECK_TEST_ROOTS",
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
    for line in result.stdout.splitlines():
        command = shlex.split(line)
        for index in range(len(command) - 2):
            if command[index : index + 2] == ["sh", "-c"]:
                command = shlex.split(command[index + 2])
                break
        commands.append(command)
    return commands


def _pytest_commands(target: str, roots: str | None = None) -> list[list[str]]:
    return [
        command[command.index("pytest") + 1 :]
        for command in _make_commands([target], roots)
        if "pytest" in command
    ]


def test_draft_feedback_uses_separate_names_and_only_cheap_native_checks() -> None:
    jobs = _workflow()["jobs"]
    draft = jobs["draft-feedback"]
    required_names = [*jobs["checks"]["strategy"]["matrix"]["target"], "build-release"]
    assert draft["name"] not in required_names
    assert "full-checks-pending-review" not in required_names
    assert draft["if"] == (
        "github.event_name == 'pull_request' && github.event.pull_request.draft == true"
    )
    assert "strategy" not in draft and "continue-on-error" not in draft
    assert all("if" not in step for step in draft["steps"])
    commands = [step["run"] for step in draft["steps"] if "run" in step]
    assert commands == [
        "make init",
        "make lint format-check architecture release-locks-check",
    ]
    native_commands = _make_commands(
        ["init", "lint", "format-check", "architecture", "release-locks-check"]
    )
    assert not any("pytest" in command for command in native_commands)


def test_hosted_backend_roots_partition_the_complete_suite_without_nested_selection() -> (
    None
):
    checks = _workflow()["jobs"]["checks"]
    selection = checks["strategy"]["matrix"]["include"]
    assert len(selection) == 1 and selection[0]["target"] == "backend-check"
    roots = selection[0]["backend_test_roots"]
    assert checks["env"]["BACKEND_CHECK_TEST_ROOTS"] == (
        "${{ matrix.backend_test_roots || '' }}"
    )
    assert "--ignore" not in checks["env"]["PYTEST_ADDOPTS"]
    assert not any(root in checks["env"]["PYTEST_ADDOPTS"] for root in roots.split())

    # Without an explicit hosted partition, pytest retains its native testpaths.
    assert _pytest_commands("backend-check") == [
        ["-m", "not certification and not slow"]
    ]
    backend = _pytest_commands("backend-check", roots)
    assert backend == [["-m", "not certification and not slow", *roots.split()]]
    integration = _pytest_commands("test-integration")
    assert integration == [
        ["-m", "not certification and not slow", "tests/integration"],
        [
            "-c",
            "backend/pyproject.toml",
            "-m",
            "not certification and not slow",
            "tests/integration",
        ],
    ]
    pytest_config = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
    testpaths = pytest_config["tool"]["pytest"]["ini_options"]["testpaths"]
    admitted_roots = [*backend[0][2:], integration[0][-1]]
    assert sorted(admitted_roots) == sorted(testpaths)
    assert len(admitted_roots) == len(set(admitted_roots))


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
    python = [
        command[command.index("pytest") + 1 :]
        for command in commands
        if "pytest" in command
    ]
    assert python == [
        ["-m", "not certification", "tests/unit", "tests/dependency_complete"],
        ["-m", "not certification", "tests/integration"],
        [
            "-c",
            "backend/pyproject.toml",
            "-m",
            "not certification",
            "tests/integration",
        ],
    ]
    batches = [
        command
        for command in commands
        if any(word.endswith("/oh-goon-test-batches") for word in command)
    ]
    assert len(batches) == 1
    assert batches[0][batches[0].index("--marker") + 1] == "not certification"
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
    assert [step["run"] for step in job["steps"] if "run" in step][-2:] == [
        "make init",
        "make release-check",
    ]
