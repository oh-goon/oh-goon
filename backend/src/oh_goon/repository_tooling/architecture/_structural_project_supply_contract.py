from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast


def _declares_path_label_option(tree: ast.Module) -> bool:
    """Check the authored public option; executable tests own argparse behavior."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and any(
            isinstance(argument, ast.Constant) and argument.value == "--path-label"
            for argument in node.args
        )
        for node in ast.walk(tree)
    )


def _check_native_command_and_run_log_presentation(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    native = root / "backend/src/oh_goon/repository_tooling/native_command.py"
    if native.is_file():
        tree = python_ast(native)
        # Source policy retains the public option declaration. Actual CLI tests
        # prove one native attempt, original argv/environment and display-only
        # path-label redaction without reconstructing call graphs or value flow.
        if not _declares_path_label_option(tree):
            violations.append(
                Violation(
                    native,
                    "native-tool display must expose the --path-label privacy protocol",
                )
            )
    run_logged = root / "scripts/run_logged.sh"
    if run_logged.is_file():
        text = run_logged.read_text(encoding="utf-8")
        # Architecture owns only observable presentation/protocol vocabulary here.
        # Streaming, argv/status preservation, redaction, durable-log separation,
        # cancellation/cleanup, retention, and persistence-failure isolation are
        # executable behavior contracts in scripts/tests/test_run_logging.py.
        for token in (
            "Working directory: <repo>",
            "<state>/run-logs",
            "<home>",
            "<tmp>",
            "OH_GOON_PROGRESS_FD",
        ):
            if token not in text:
                violations.append(
                    Violation(
                        run_logged,
                        f"normal run-log presentation must preserve observable privacy/progress protocol: {token}",
                    )
                )
    return violations


def _check_certification_progress(root: Path) -> list[Violation]:
    plan_cli = root / "backend/src/oh_goon/cli_runtime_plans.py"
    if not plan_cli.is_file():
        return []
    text = plan_cli.read_text(encoding="utf-8")
    # Source policy owns the public presentation protocol. Actual CLI and
    # Game Tape projection tests protect the live Run boundary; this owner does
    # not reconstruct a call graph or imported binding authority.
    required = ("OH_GOON_PROGRESS_FD",)
    violations = [
        Violation(
            plan_cli,
            f"certification progress must remain live presentation over existing Run/Game Tape owners: {token}",
        )
        for token in required
        if token not in text
    ]
    return violations


def check_project_supply_contract(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(_check_native_command_and_run_log_presentation(root))
    violations.extend(_check_certification_progress(root))
    return violations
