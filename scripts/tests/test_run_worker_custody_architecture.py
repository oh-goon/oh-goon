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
        ("_application_worker.py", "bind_process_child_custody()", "None"),
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


@pytest.mark.parametrize(
    "source",
    [
        "from oh_goon.services.fleet_run_execution import FleetRunExecution\n"
        "def execute_local(execution: FleetRunExecution, ticket):\n"
        "    return execution.execute(ticket)\n",
        "from oh_goon.services.fleet_run_execution import FleetRunExecution as RunOwner\n"
        "def execute_local(execution: RunOwner, ticket):\n"
        "    return execution.execute(ticket)\n",
        "import oh_goon.services.fleet_run_execution as owner\n"
        "def execute_local(*args):\n    return owner.FleetRunExecution(*args)\n",
        "from oh_goon.services import fleet_run_execution as owner\n"
        "def execute_local(*args):\n    return owner.FleetRunExecution(*args)\n",
        "from .fleet_run_execution import FleetRunExecution as RunOwner\n"
        "def execute_local(*args):\n    return RunOwner(*args)\n",
        "from . import fleet_run_execution as owner\n"
        "def execute_local(*args):\n    return owner.FleetRunExecution(*args)\n",
        "from ..services.fleet_run_execution import FleetRunExecution as RunOwner\n"
        "def execute_local(*args):\n    return RunOwner(*args)\n",
        "import oh_goon.services as services\n"
        "RunOwner = services.fleet_run_execution.FleetRunExecution\n",
        "from oh_goon.services.fleet_run_execution import *\n",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .fleet_run_execution import FleetRunExecution as RunOwner\n",
        "from .fleet_run_execution import FleetRunExecution as RunOwner\n"
        "RunOwner = object\n",
        "import oh_goon.services as services\n"
        "RunOwner = getattr(services, 'fleet_run_execution').FleetRunExecution\n",
        "from oh_goon._application_worker_runtime import build_claimed_run_execution\n"
        "def execute_local(*args):\n"
        "    return build_claimed_run_execution(*args).execute(args[1])\n",
        "from .. import _application_worker_runtime as composition\n"
        "def execute_local(*args):\n"
        "    return composition.build_claimed_run_execution(*args).execute(args[1])\n",
    ],
)
def test_additional_production_module_cannot_acquire_run_execution_authority(
    baseline: Path, source: str
) -> None:
    path = baseline / "services/additional_runs.py"
    path.write_text(source)

    issues = _check_run_worker_custody(baseline)

    assert any(
        issue.path == path and "outside canonical" in issue.message for issue in issues
    )


@pytest.mark.parametrize(
    "owner", ("_application_services.py", "_application_worker_runtime.py")
)
def test_canonical_composition_can_alias_its_execution_owner(
    baseline: Path, owner: str
) -> None:
    path = baseline / owner
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "oh_goon.services.fleet_run_execution"
        ):
            for imported in node.names:
                if imported.name == "FleetRunExecution":
                    imported.asname = "RunOwner"
        elif isinstance(node, ast.Name) and node.id == "FleetRunExecution":
            node.id = "RunOwner"
    path.write_text(ast.unparse(tree) + "\n")

    assert _check_run_worker_custody(baseline) == []


def test_ordinary_module_can_use_unrelated_execute_and_local_class_names(
    baseline: Path,
) -> None:
    path = baseline / "services/diagnostic_projection.py"
    path.write_text(
        "# from oh_goon.services.fleet_run_execution import FleetRunExecution\n"
        "note = 'oh_goon._application_worker_runtime.build_claimed_run_execution'\n"
        "class FleetRunExecution:\n"
        "    def execute(self, value):\n        return value\n"
        "def diagnostic():\n"
        "    renamed_local_owner = FleetRunExecution()\n"
        "    return renamed_local_owner.execute(note)\n"
    )

    assert _check_run_worker_custody(baseline) == []


def test_installed_repository_tooling_cannot_acquire_run_execution_authority(
    baseline: Path,
) -> None:
    path = baseline / "repository_tooling/alternate_run.py"
    path.parent.mkdir()
    path.write_text(
        "from ..services.fleet_run_execution import FleetRunExecution as RunOwner\n"
        "def execute_local(execution: RunOwner, ticket):\n"
        "    return execution.execute(ticket)\n"
    )

    issues = _check_run_worker_custody(baseline)

    assert any(
        issue.path == path and "outside canonical" in issue.message for issue in issues
    )


def test_repository_tooling_authority_strings_are_not_imported_references(
    baseline: Path,
) -> None:
    path = baseline / "repository_tooling/authority_contract.py"
    path.parent.mkdir()
    path.write_text(
        "# from oh_goon.services.fleet_run_execution import FleetRunExecution\n"
        "owners = {\n"
        "    'oh_goon.services.fleet_run_execution': {'_application_services.py'},\n"
        "    'oh_goon._application_worker_runtime': {'_application_worker.py'},\n"
        "}\n"
    )

    assert _check_run_worker_custody(baseline) == []


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\n"
        "def execute_local(*args):\n"
        "    owner = importlib.import_module('oh_goon.services.fleet_run_execution')\n"
        "    return owner.FleetRunExecution(*args)\n",
        "from importlib import import_module as load\n"
        "owner = load('oh_goon.services.fleet_run_execution')\n",
        "import importlib as imports\n"
        "owner = imports.import_module(name='.fleet_run_execution', package='oh_goon.services')\n",
        "import builtins\n"
        "owner = builtins.__import__('oh_goon.services.fleet_run_execution', "
        "fromlist=['FleetRunExecution'])\n",
        "from builtins import __import__ as load\n"
        "owner = load('oh_goon.services', fromlist=['fleet_run_execution'])\n",
        "from importlib import import_module as load\n"
        "builder = load('oh_goon._application_worker_runtime').build_claimed_run_execution\n",
        "import builtins as native\n"
        "builder = native.__import__('oh_goon', fromlist=['_application_worker_runtime'])\n",
    ],
)
def test_qualified_literal_dynamic_import_cannot_acquire_run_authority(
    baseline: Path, source: str
) -> None:
    path = baseline / "services/additional_runs.py"
    path.write_text(source)

    issues = _check_run_worker_custody(baseline)

    assert any(
        issue.path == path and "outside canonical" in issue.message for issue in issues
    )


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\n"
        "def local(importlib):\n"
        "    return importlib.import_module('oh_goon.services.fleet_run_execution')\n",
        "from importlib import import_module as load\n"
        "def local(load):\n"
        "    return load('oh_goon.services.fleet_run_execution')\n",
        "import importlib\n"
        "def local(loader=importlib.import_module):\n"
        "    return loader('oh_goon.services.fleet_run_execution')\n",
        "import importlib\n"
        "def local(name):\n"
        "    return importlib.import_module(name)\n"
        "note = 'oh_goon.services.fleet_run_execution'\n",
        "def import_module(name):\n    return name\n"
        "import_module('oh_goon.services.fleet_run_execution')\n",
        "from pathlib import Path\n"
        "note = Path('oh_goon._application_worker_runtime')\n",
        "import builtins\n"
        "def local(builtins):\n"
        "    return builtins.__import__('oh_goon.services.fleet_run_execution')\n",
    ],
)
def test_unknown_or_shadowed_dynamic_calls_do_not_become_import_authority(
    baseline: Path, source: str
) -> None:
    path = baseline / "services/diagnostic_projection.py"
    path.write_text(source)

    assert _check_run_worker_custody(baseline) == []
