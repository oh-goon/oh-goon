from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture._launch_contract_common import (
    owned_string_constant_violations,
    forbidden_import_violations,
    python_call_paths,
)
from oh_goon.repository_tooling.architecture.common import Violation, python_ast


def check_launch_activity_control(root: Path) -> list[Violation]:
    violations = owned_string_constant_violations(
        root,
        {root / "backend/src/oh_goon/core/launch_activity.py": ("LAUNCH_LOG_SCHEMA",)},
        missing_message="durable Launch log schema owner is missing or invalid",
        duplicate_message="Launch log schema must have one natural owner",
    )
    for path in (
        root / "backend/src/oh_goon/core/launch_control.py",
        root / "backend/src/oh_goon/cli_stop.py",
    ):
        violations.extend(
            forbidden_import_violations(
                path,
                ("subprocess",),
                message_prefix="Launch stop request must not become generic process authority",
            )
        )
        if "os.kill" in python_call_paths(path):
            violations.append(
                Violation(
                    path,
                    "Launch stop request must not become generic PID signal authority",
                )
            )
    return violations


def check_launch_birth_identity(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in (
        root / "backend/src/oh_goon/core/process_birth.py",
        root / "backend/src/oh_goon/core/launch_status.py",
    ):
        if path.is_file() and _contains_pid_signal_probe(path):
            violations.append(
                Violation(
                    path,
                    "Launch owner proof must use process birth identity; PID existence/signal checks are not process identity",
                )
            )
    return violations


def _contains_pid_signal_probe(path: Path) -> bool:
    tree = python_ast(path)
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        func = call.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "os" and func.attr in {"kill", "killpg"}:
                return True
        if isinstance(func, ast.Name) and func.id in {"kill", "killpg"}:
            return True
    return False


def check_launch_current_selection(root: Path) -> list[Violation]:
    """Current-launch selection behavior is executable-tested; architecture owns no helper spelling."""

    return []
