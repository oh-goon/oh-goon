from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    python_semantic_facts,
)


def _calls(path: Path, *qualified_names: str) -> bool:
    wanted = frozenset(qualified_names)
    return any(name in wanted for name, _node in python_semantic_facts(path).calls)


def _references(path: Path, *qualified_names: str) -> bool:
    wanted = tuple(qualified_names)
    return any(
        reference == prefix or reference.startswith(prefix + ".")
        for reference in python_semantic_facts(path).references
        for prefix in wanted
    )


def _check_self_verification_accelerator(root: Path) -> list[Violation]:
    """Self verification must not grow a second command-execution path."""

    violations: list[Violation] = []
    cli = root / "backend/src/oh_goon/cli_self.py"
    if cli.is_file():
        if _calls(cli, "subprocess.run", "subprocess.Popen"):
            violations.append(
                Violation(
                    cli,
                    "self CLI must delegate execution through normal Plan authority, not launch child commands directly",
                )
            )

    parser = root / "backend/src/oh_goon/cli_parser.py"
    if parser.is_file():
        has_run_unit = any(
            name.endswith(".add_argument")
            and any(
                isinstance(argument, ast.Constant) and argument.value == "--run-unit"
                for argument in node.args
            )
            for name, node in python_semantic_facts(parser).calls
        )
        if has_run_unit:
            violations.append(
                Violation(
                    parser,
                    "public --run-unit bypass must be absent; canonical Plan execution owns Step execution",
                )
            )
    return violations


def _check_certification_preexecution_authority(root: Path) -> list[Violation]:
    """Protect public certification frontdoors and forbid alternate orchestration paths.

    Detailed admission, SQLite identity, lease, and no-work-before-admission semantics are
    behavior contracts and are tested at their owners.  This architecture check therefore
    avoids pinning private helper names or implementation choreography.
    """

    violations: list[Violation] = []
    makefile = root / "Makefile"
    if not makefile.is_file():
        violations.append(Violation(makefile, "repository Make frontdoor is missing"))
    else:
        text = makefile.read_text(encoding="utf-8")
        for target in (
            "goon-static-admission:",
            "certify:",
            "prepare-release:",
            "certification-state-reset:",
        ):
            if target not in text:
                violations.append(
                    Violation(makefile, f"public certification frontdoor is missing: {target}")
                )

    workflow = root / "backend/src/oh_goon/workflow.py"
    if workflow.is_file():
        imports = python_semantic_facts(workflow).imports
        if any(module.startswith("oh_goon.services.") for module in imports):
            violations.append(
                Violation(
                    workflow,
                    "workflow orchestration must not import service/domain ownership to implement certification control state",
                )
            )

    frontdoor = root / "backend/src/oh_goon/services/certification_frontdoor.py"
    if frontdoor.is_file() and _calls(frontdoor, "subprocess.Popen", "subprocess.run"):
        violations.append(
            Violation(
                frontdoor,
                "certification frontdoor must use the admitted command/process authority instead of owning a second subprocess path",
            )
        )
    return violations


def _check_execution_authority_ii(root: Path) -> list[Violation]:
    """Strict execution must not regain ambient environment or direct subprocess authority."""

    violations: list[Violation] = []
    strict_modules = (
        root / "backend/src/oh_goon/integrations/_fleet_plan_step_local_execution.py",
        root / "backend/src/oh_goon/integrations/_fleet_plan_step_retry.py",
        root / "backend/src/oh_goon/integrations/fleet_plan_step_publication.py",
    )
    for path in strict_modules:
        if not path.is_file():
            continue
        if _references(path, "os.environ") or _calls(path, "os.getenv"):
            violations.append(
                Violation(
                    path,
                    "strict Step execution must consume frozen prepared environment, never ambient process environment",
                )
            )
        if _calls(path, "subprocess.Popen", "subprocess.run"):
            violations.append(
                Violation(
                    path,
                    "strict Step execution must delegate process lifecycle to admitted command/isolation authority",
                )
            )
    return violations


def _check_scheduler_backpressure_authority(root: Path) -> list[Violation]:
    """Strict placement must not use raw wall-clock polling.

    Queue capacity and eligibility semantics are behavior-tested at the queue owner; private
    helper names and SQL snippets are intentionally not architecture contracts.
    """

    violations: list[Violation] = []
    placement = root / "backend/src/oh_goon/runtime/_executor_placement.py"
    if placement.is_file() and _calls(placement, "time.monotonic", "time.sleep"):
        violations.append(
            Violation(
                placement,
                "strict executor placement must use shared monotonic clock/wait authority, never raw time polling",
            )
        )
    return violations
