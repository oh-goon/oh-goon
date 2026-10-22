from __future__ import annotations

import re
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


def test_required_checks_run_for_every_pull_request_and_main_commit() -> None:
    workflow = _workflow()
    assert workflow["on"] == {
        "pull_request": None,
        "push": {"branches": ["main"]},
        "merge_group": None,
    }
    assert set(workflow["jobs"]) == {"checks"}
    checks = workflow["jobs"]["checks"]
    assert checks["name"] == "${{ matrix.target }}"
    assert checks["strategy"]["fail-fast"] is False
    assert set(checks["strategy"]["matrix"]["target"]) == {
        "backend-check",
        "frontend-check",
        "tooling-check",
        "architecture",
        "check-generated",
        "test-integration",
        "build-release",
    }
    assert "if" not in checks
    assert "continue-on-error" not in checks
    for step in checks["steps"]:
        assert "if" not in step
        assert "continue-on-error" not in step
    commands = [step["run"] for step in checks["steps"] if "run" in step]
    assert "make init" in commands
    assert commands[-1] == 'make "${{ matrix.target }}"'


def test_pull_request_checks_have_read_only_uncredentialed_execution() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    checks = workflow["jobs"]["checks"]
    assert "permissions" not in checks
    assert "secrets" not in checks
    for step in checks["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
    checkout = next(
        step
        for step in checks["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["fetch-depth"] == 0
