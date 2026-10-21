from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest

from oh_goon import cli_parser
from oh_goon.repository_tooling import _self_certification_plan_units as contract
from oh_goon.repository_tooling import self_certification_plan as plan_module

_CONTRACT_NAMES = {
    "PLAN_NAME",
    "SCRIPTS_SURFACE",
    "SHARD_STRATEGY",
    "MAX_TESTS_PER_SHARD",
    "SCRIPTS_BUDGET_SECONDS",
    "STATIC_GOON_ADMISSION_UNIT",
    "INHERITED_CLOSURE_CONTRACTS",
}


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_self_certification_contract_owner_change_reaches_all_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling import self_certification_artifacts

    root = _root()
    metadata = tmp_path / "checkpoint-parent.json"
    metadata.write_text(
        json.dumps(
            {
                "schema": 1,
                "version": "0.9.0",
                "artifact": "oh-goon-0.9.0.zip",
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_certification_artifacts, "PARENT_METADATA", metadata)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    baseline = plan_module.self_certification_plan(root)

    monkeypatch.setattr(contract, "PLAN_NAME", "builtin:self-verification-m91-test")
    monkeypatch.setattr(contract, "SCRIPTS_SURFACE", "scripts-m91-test")
    monkeypatch.setattr(contract, "SHARD_STRATEGY", "deterministic-m91-test")
    monkeypatch.setattr(contract, "MAX_TESTS_PER_SHARD", 13)
    monkeypatch.setattr(contract, "SCRIPTS_BUDGET_SECONDS", 241)
    monkeypatch.setattr(
        contract, "STATIC_GOON_ADMISSION_UNIT", "goon-static-admission-m91-test"
    )
    monkeypatch.setattr(
        contract,
        "INHERITED_CLOSURE_CONTRACTS",
        {"m91-owner-test": ("one-semantic-owner",)},
    )

    changed = plan_module.self_certification_plan(root)
    surfaces = changed["surfaces"]
    assert isinstance(surfaces, list)
    shard_projection = surfaces[0]
    script_tests = sorted((root / "scripts/tests").glob("test_*.py"))

    assert changed["plan"] == "builtin:self-verification-m91-test"
    assert changed["inherited_closure_contracts"] == {
        "m91-owner-test": ["one-semantic-owner"]
    }
    assert (
        changed["verification_config_identity"]
        != baseline["verification_config_identity"]
    )
    assert changed["plan_identity"] != baseline["plan_identity"]
    assert shard_projection["surface"] == "scripts-m91-test"
    assert shard_projection["strategy"] == "deterministic-m91-test"
    assert len(shard_projection["shards"]) == math.ceil(len(script_tests) / 13)
    assert {
        item["execution_budget_seconds"] for item in shard_projection["shards"]
    } == {241}

    all_units = changed["all_units"]
    assert isinstance(all_units, list)
    source_units = [item for item in all_units if item.get("phase") == "source"]
    assert source_units[0]["unit"] == "goon-static-admission-m91-test"
    assert all(
        item.get("requires") == ["goon-static-admission-m91-test"]
        for item in source_units[1:]
    )

    parser = cli_parser.build_parser()
    parsed = parser.parse_args(["dev", "shards", "scripts-m91-test"])
    assert parsed.surface == "scripts-m91-test"
    with pytest.raises(SystemExit):
        parser.parse_args(["dev", "shards", "scripts-non-certification"])


def test_self_certification_contract_has_one_production_assignment_owner() -> None:
    root = _root()
    source_root = root / "backend/src/oh_goon"
    owner = (
        (
            root
            / "backend/src/oh_goon/repository_tooling/_self_certification_plan_units.py"
        )
        .relative_to(root)
        .as_posix()
    )
    assignments: dict[str, list[str]] = {name: [] for name in _CONTRACT_NAMES}

    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets.append(node.target)
            for target in targets:
                if isinstance(target, ast.Name) and target.id in assignments:
                    assignments[target.id].append(path.relative_to(root).as_posix())

    assert assignments == {name: [owner] for name in _CONTRACT_NAMES}
