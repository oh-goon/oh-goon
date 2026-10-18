from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/development/TEST_SETUP_ISOLATION_QA.yml"


def _autouse_fixture_metadata(path: Path) -> tuple[str, bool]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fixtures: list[ast.Call] = []
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for item in function.decorator_list:
            if not (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id == "pytest"
                and item.func.attr == "fixture"
            ):
                continue
            keywords = {entry.arg: entry.value for entry in item.keywords if entry.arg is not None}
            autouse_node = keywords.get("autouse")
            if isinstance(autouse_node, ast.Constant) and autouse_node.value is True:
                fixtures.append(item)
    assert len(fixtures) == 1, f"expected one autouse isolation fixture in {path}, found {len(fixtures)}"
    decorator = fixtures[0]
    keywords = {item.arg: item.value for item in decorator.keywords if item.arg is not None}
    scope_node = keywords.get("scope")
    autouse_node = keywords.get("autouse")
    scope = str(scope_node.value) if isinstance(scope_node, ast.Constant) else "function"
    autouse = bool(autouse_node.value) if isinstance(autouse_node, ast.Constant) else False
    return scope, autouse


def test_isolation_fixture_policy_keeps_current_owners_function_scoped() -> None:
    payload = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    assert payload["version"] == "test-setup-isolation-qa.v1"
    assert "meaningful" in payload["policy"]["scope_change_rule"].lower()
    assert "no-state-leakage" in payload["policy"]["scope_change_rule"].lower()

    rows = payload["fixtures"]
    assert len(rows) == 3
    for row in rows:
        path = ROOT / row["path"]
        assert path.is_file(), row
        assert row["expected_scope"] == "function", row
        assert row["autouse"] is True, row
        assert row["reason"].strip(), row
        scope, autouse = _autouse_fixture_metadata(path)
        assert scope == row["expected_scope"], row
        assert autouse is row["autouse"], row


def test_scope_widening_requires_ledger_change_and_explicit_evidence() -> None:
    payload = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    rule = payload["policy"]["scope_change_rule"].lower()
    for required in (
        "no-state-leakage regression",
        "ordering/retry/failure",
        "meaningful",
        "micro-optimisations",
    ):
        assert required in rule
