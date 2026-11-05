"""The existing X0 owner rejects observable Run-boundary bypasses."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests.factories.x0_architecture import replace_python_syntax

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_run_worker_custody,
)


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    package = tmp_path / "backend/src/oh_goon"
    for relative in (
        "services/fleet_run_control_plane.py",
        "runtime/execution/run_supervisor.py",
        "runtime/execution/coordinator.py",
        "services/fleet_run_lifecycle.py",
        "_application_worker.py",
        "_application_worker_runtime.py",
        "_application_services.py",
    ):
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / "backend/src/oh_goon" / relative).read_text())
    assert _check_run_worker_custody(package) == []
    return package


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        ("_application_worker.py", "LocalDatabase.open_worker(", "LocalDatabase("),
        ("_application_worker.py", ".claim_fleet_worker(", ".activate("),
        (
            "_application_worker.py",
            "execution.execute(claimed)",
            "execution.execute_direct(claimed)",
        ),
        (
            "_application_worker_runtime.py",
            "execution.require_claimed_fleet_worker(claimed_ticket)",
            "None",
        ),
        (
            "runtime/execution/coordinator.py",
            "self._leases.assert_executable_owner(ticket._lease)",
            "self._leases.assert_owner(ticket._lease)",
        ),
        (
            "services/fleet_run_lifecycle.py",
            "self._execution.activate_submission_failure(ticket)",
            "self._execution.activate(ticket)",
        ),
    ],
)
def test_removed_canonical_boundary_rejected(
    baseline: Path, relative: str, old: str, new: str
) -> None:
    path = baseline / relative
    path.write_text(replace_python_syntax(path.read_text(), old, new))
    assert _check_run_worker_custody(baseline)


@pytest.mark.parametrize(
    ("relative", "extra"),
    [
        (
            "services/fleet_run_control_plane.py",
            "def bypass(self, ticket):\n    self._run_execution.execute(ticket)\n",
        ),
        (
            "services/fleet_run_control_plane.py",
            'def bypass(self, ticket):\n    self._supervisor.submit("run", lambda: work(ticket))\n',
        ),
        (
            "runtime/execution/run_supervisor.py",
            "from concurrent.futures import ThreadPoolExecutor as WorkerPool\n"
            "def bypass():\n    return WorkerPool(max_workers=1)\n",
        ),
        (
            "runtime/execution/run_supervisor.py",
            "import concurrent.futures as pools\n"
            "def bypass():\n    return pools.ProcessPoolExecutor(max_workers=1)\n",
        ),
        (
            "_application_worker.py",
            "from oh_goon.application import ApplicationRuntime\n"
            "def bypass():\n    return ApplicationRuntime.build()\n",
        ),
    ],
)
def test_second_execution_or_startup_path_rejected(
    baseline: Path, relative: str, extra: str
) -> None:
    path = baseline / relative
    path.write_text(path.read_text() + "\n" + extra)
    assert _check_run_worker_custody(baseline)


def test_product_cannot_omit_captured_worker_submission(baseline: Path) -> None:
    path = baseline / "_application_services.py"
    tree = ast.parse(path.read_text())
    changed = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "FleetRunControlPlane"
        ):
            original = len(node.keywords)
            node.keywords = [
                keyword
                for keyword in node.keywords
                if keyword.arg != "worker_configuration"
            ]
            changed = changed or len(node.keywords) != original
    assert changed
    path.write_text(ast.unparse(tree) + "\n")
    assert _check_run_worker_custody(baseline)


def test_claim_must_precede_effectful_composition(baseline: Path) -> None:
    path = baseline / "_application_worker_runtime.py"
    tree = ast.parse(path.read_text())
    builder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_claimed_run_execution"
    )
    claimed = next(
        node
        for node in builder.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "require_claimed_fleet_worker"
    )
    builder.body.remove(claimed)
    builder.body.insert(-1, claimed)
    path.write_text(ast.unparse(tree) + "\n")
    assert _check_run_worker_custody(baseline)


def test_comments_and_private_renames_do_not_decide_custody(baseline: Path) -> None:
    for relative in ("_application_worker.py", "_application_worker_runtime.py"):
        path = baseline / relative
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "claimed":
                node.id = "worker_ticket"
        path.write_text(
            "# ThreadPoolExecutor executes work: prose is not authority.\n"
            + ast.unparse(tree)
            + "\n"
        )
    assert _check_run_worker_custody(baseline) == []
