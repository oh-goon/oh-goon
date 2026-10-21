from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from oh_goon.repository_tooling import (
    hosted_diagnostic_receipt,
    pytest_batch_orchestration,
    pytest_execution_report,
)

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = ROOT / "backend/src/oh_goon"
FAMILY = "oh-goon.pytest-batch-execution"


def _schema() -> str:
    return pytest_execution_report.execution_report_schema()


def _report_payload(schema: str) -> dict[str, object]:
    members = ["a.py"]
    return {
        "schema": schema,
        "status": "PASS",
        "source_identity": "sha256:source",
        "test_plan_identity": "sha256:plan",
        "toolchain_identity": "sha256:tools",
        "execution_plan_identity": "sha256:execution",
        "membership_receipt": {
            "selected": members,
            "attempted": members,
            "completed": members,
            "passed": members,
            "failed": [],
            "non_passed": [],
            "resumed": [],
            "missing": [],
            "duplicates": [],
            "extras": [],
            "selected_membership_identity": "sha256:selected",
            "terminal_membership_identity": "sha256:terminal",
            "terminal_success": True,
        },
    }


def test_execution_report_projection_follows_schema_owner_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pytest_execution_report,
        "PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION",
        7,
    )
    args = SimpleNamespace(
        granularity="files",
        batch_timeout=10,
        suite_timeout=20,
        no_timeout_bisection=False,
        environment_retries=1,
        resume=False,
        resume_key="unused",
    )
    plan = SimpleNamespace(
        reusable_members=frozenset(),
        selected_members=("a.py",),
        resume_context=SimpleNamespace(
            source_identity="sha256:source",
            test_plan_identity="sha256:plan",
            toolchain_identity="sha256:tools",
            suite_identity="sha256:suite",
        ),
        execution_plan_identity="sha256:execution",
        run_identity="sha256:run",
    )
    state = SimpleNamespace(
        failures=0,
        suite_timed_out=False,
        batch_reports=[],
    )
    receipt = SimpleNamespace(
        as_dict=lambda: _report_payload(_schema())["membership_receipt"]
    )

    payload = pytest_batch_orchestration._execution_report(
        args,
        cast(pytest_batch_orchestration._BatchPlan, plan),
        cast(pytest_batch_orchestration._BatchRunState, state),
        receipt,
        Path("receipt.json"),
        0.1,
    )

    assert payload["schema"] == f"{FAMILY}/v7"


def test_execution_report_persistence_rejects_stale_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        pytest_execution_report,
        "PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION",
        7,
    )
    report = tmp_path / "report.json"

    with pytest.raises(
        RuntimeError, match="execution report schema does not match owner"
    ):
        pytest_execution_report.write_execution_report(
            report,
            _report_payload(f"{FAMILY}/v1"),
        )
    assert not report.exists()

    pytest_execution_report.write_execution_report(report, _report_payload(_schema()))
    assert json.loads(report.read_text(encoding="utf-8"))["schema"] == f"{FAMILY}/v7"


def test_hosted_receipt_validation_follows_schema_owner_and_rejects_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        pytest_execution_report,
        "PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION",
        7,
    )
    report = tmp_path / "batch.json"
    report.write_text(json.dumps(_report_payload(_schema())), encoding="utf-8")
    loaded = hosted_diagnostic_receipt._load_batch_report("backend-unit", 1, report)
    assert loaded["status"] == "PASS"

    report.write_text(json.dumps(_report_payload(f"{FAMILY}/v1")), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported batch report schema"):
        hosted_diagnostic_receipt._load_batch_report("backend-unit", 1, report)


def test_execution_report_owner_fails_closed_on_invalid_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pytest_execution_report,
        "PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION",
        0,
    )
    with pytest.raises(RuntimeError, match="schema version owner is invalid"):
        _schema()


def test_production_has_one_pytest_batch_execution_report_schema_owner() -> None:
    family_literals: list[tuple[Path, int]] = []
    family_assignments: list[tuple[Path, int]] = []
    version_assignments: list[tuple[Path, int]] = []
    legacy_batch_schema_assignments: list[tuple[Path, int]] = []
    full_v1_literals: list[tuple[Path, int]] = []

    for path in PRODUCTION_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if FAMILY in node.value:
                    family_literals.append((path, node.lineno))
                if node.value == f"{FAMILY}/v1":
                    full_v1_literals.append((path, node.lineno))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                names = [
                    target.id for target in targets if isinstance(target, ast.Name)
                ]
                if "PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_FAMILY" in names:
                    family_assignments.append((path, node.lineno))
                if "PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION" in names:
                    version_assignments.append((path, node.lineno))
                if "BATCH_SCHEMA" in names:
                    legacy_batch_schema_assignments.append((path, node.lineno))

    assert family_assignments == [
        (
            PRODUCTION_ROOT / "repository_tooling/pytest_execution_report.py",
            family_assignments[0][1],
        )
    ]
    assert version_assignments == [
        (
            PRODUCTION_ROOT / "repository_tooling/pytest_execution_report.py",
            version_assignments[0][1],
        )
    ]
    assert family_literals == [
        (
            PRODUCTION_ROOT / "repository_tooling/pytest_execution_report.py",
            family_literals[0][1],
        )
    ]
    assert not legacy_batch_schema_assignments
    assert not full_v1_literals
