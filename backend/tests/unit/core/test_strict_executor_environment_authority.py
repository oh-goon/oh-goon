from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.core import strict_executor_authority

SOURCE_ROOT = Path(strict_executor_authority.__file__).resolve().parents[1]
OWNER = Path(strict_executor_authority.__file__).resolve()


def test_executor_inventory_environment_identity_has_one_production_spelling() -> None:
    """The public environment identity is authored once; consumers import the owner symbol."""

    value = strict_executor_authority.EXECUTORS_FILE_ENV
    occurrences: list[tuple[Path, int]] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        occurrences.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == value
        )

    assert len(occurrences) == 1
    assert occurrences[0][0] == OWNER


def test_executor_inventory_environment_owner_is_the_named_string_assignment() -> None:
    tree = ast.parse(OWNER.read_text(encoding="utf-8"), filename=str(OWNER))
    assignments: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if (
            not isinstance(value, ast.Constant)
            or value.value != strict_executor_authority.EXECUTORS_FILE_ENV
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        assignments.extend(target.id for target in targets if isinstance(target, ast.Name))

    assert assignments == ["EXECUTORS_FILE_ENV"]
