from __future__ import annotations

import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import Any

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
    assert commands[-1] == 'make "${{ matrix.target }}"'


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
    environment.pop("BACKEND_CHECK_TEST_ROOTS", None)
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
    assert _pytest_commands("backend-check") == [["-m", "not certification"]]
    backend = _pytest_commands("backend-check", roots)
    assert backend == [["-m", "not certification", *roots.split()]]
    integration = _pytest_commands("test-integration")
    assert integration == [
        ["-m", "not certification", "tests/integration"],
        [
            "-c",
            "backend/pyproject.toml",
            "-m",
            "not certification",
            "tests/integration",
        ],
    ]
    pytest_config = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
    testpaths = pytest_config["tool"]["pytest"]["ini_options"]["testpaths"]
    admitted_roots = [*backend[0][2:], integration[0][-1]]
    assert sorted(admitted_roots) == sorted(testpaths)
    assert len(admitted_roots) == len(set(admitted_roots))
