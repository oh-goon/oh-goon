from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from oh_goon import cli_parser
from oh_goon.api.schemas.repositories import ExcludeRepositoryRequest
from oh_goon.core import engine_policy_defaults as exclusion_policy
from oh_goon.runtime.exclusions import ensure_exclusion_schema, write_exclusion

ROOT = Path(__file__).resolve().parents[2]
OWNER_PATH = ROOT / "backend/src/oh_goon/core/engine_policy_defaults.py"


def test_owner_mutation_flows_through_cli_api_and_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        exclusion_policy, "DEFAULT_EXCLUSION_REASON", "Owner-defined exclusion"
    )

    args = cli_parser.build_parser().parse_args(["exclude", "/tmp/example"])
    assert args.reason == "Owner-defined exclusion"

    request = ExcludeRepositoryRequest(
        repository="/tmp/example",
        expected_repository_instance_id="instance-1",
    )
    assert request.reason == "Owner-defined exclusion"

    connection = sqlite3.connect(":memory:")
    ensure_exclusion_schema(connection)
    record = write_exclusion(connection, "/tmp/example", "")
    assert record["reason"] == "Owner-defined exclusion"


def test_explicit_reason_narrows_without_redeciding_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        exclusion_policy, "DEFAULT_EXCLUSION_REASON", "Owner-defined exclusion"
    )

    args = cli_parser.build_parser().parse_args(
        ["exclude", "/tmp/example", "--reason", "maintenance"]
    )
    assert args.reason == "maintenance"

    request = ExcludeRepositoryRequest(
        repository="/tmp/example",
        expected_repository_instance_id="instance-1",
        reason="maintenance",
    )
    assert request.reason == "maintenance"

    connection = sqlite3.connect(":memory:")
    ensure_exclusion_schema(connection)
    record = write_exclusion(connection, "/tmp/example", "  maintenance  ")
    assert record["reason"] == "maintenance"


@pytest.mark.parametrize("malformed", ["", " padded ", 17])
def test_malformed_owner_fails_closed_every_default_surface(
    monkeypatch: pytest.MonkeyPatch,
    malformed: object,
) -> None:
    monkeypatch.setattr(exclusion_policy, "DEFAULT_EXCLUSION_REASON", malformed)

    with pytest.raises(
        RuntimeError, match="default exclusion reason policy is malformed"
    ):
        cli_parser.build_parser()
    with pytest.raises(
        RuntimeError, match="default exclusion reason policy is malformed"
    ):
        ExcludeRepositoryRequest(
            repository="/tmp/example",
            expected_repository_instance_id="instance-1",
        )
    connection = sqlite3.connect(":memory:")
    ensure_exclusion_schema(connection)
    with pytest.raises(
        RuntimeError, match="default exclusion reason policy is malformed"
    ):
        write_exclusion(connection, "/tmp/example", "")


def test_exclusion_default_has_one_authored_assignment_and_literal() -> None:
    assignments: list[Path] = []
    default_literals: list[Path] = []
    legacy_assignments: list[Path] = []

    for path in (ROOT / "backend/src/oh_goon").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                target_names = [
                    target.id for target in node.targets if isinstance(target, ast.Name)
                ]
                if "DEFAULT_EXCLUSION_REASON" in target_names:
                    assignments.append(path)
                if "DEFAULT_REASON" in target_names:
                    legacy_assignments.append(path)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "DEFAULT_EXCLUSION_REASON":
                    assignments.append(path)
                if node.target.id == "DEFAULT_REASON":
                    legacy_assignments.append(path)
            if isinstance(node, ast.Constant) and node.value == "Excluded by user":
                default_literals.append(path)

    assert assignments == [OWNER_PATH]
    assert default_literals == [OWNER_PATH]
    assert legacy_assignments == []
