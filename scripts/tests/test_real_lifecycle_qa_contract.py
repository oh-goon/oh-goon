from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/development/REAL_LIFECYCLE_QA.yml"


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_real_lifecycle_ledger_is_complete_and_refs_real_pytest_nodes() -> None:
    payload = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    assert payload["version"] == "real-lifecycle-qa.v2"
    assert "real lifecycle" in payload["policy"]["principle"].lower()
    rows = payload["lifecycles"]
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)) >= 12

    for row in rows:
        required = set(row["required_modes"])
        proofs = row.get("proofs", [])
        if row.get("external_gate"):
            assert required == {"native"}
            assert row.get("reason")
            continue
        observed = {proof["mode"] for proof in proofs}
        assert required <= observed, (row["id"], required - observed)
        for proof in proofs:
            if "test" in proof:
                path_text, separator, test_name = proof["test"].partition("::")
                assert separator and test_name.startswith("test_"), proof
                path = ROOT / path_text
                assert path.is_file(), proof
                assert test_name in _functions(path), proof
                continue
            command = proof.get("command")
            assert isinstance(command, str) and command.startswith("make "), proof
            target = command.removeprefix("make ").strip()
            makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
            assert f"\n{target}:" in makefile, proof


def test_critical_lifecycles_are_not_claimed_by_unit_only_proof() -> None:
    payload = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    for row in payload["lifecycles"]:
        if row.get("external_gate"):
            continue
        modes = {proof["mode"] for proof in row["proofs"]}
        assert modes & {"lifecycle", "boundary", "adversarial", "mutation"}, row["id"]
        assert "unit" not in row["required_modes"]


def test_high_risk_historical_regressions_have_mutation_proof() -> None:
    payload = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in payload["lifecycles"]}
    for lifecycle_id in (
        "bootstrap-convergence",
        "sqlite-preparation-execution-lifetime",
    ):
        modes = {proof["mode"] for proof in by_id[lifecycle_id]["proofs"]}
        assert "mutation" in modes


def test_real_lifecycle_makefile_explicit_pytest_nodes_exist() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\ntest-real-lifecycle:\n", 1)[1].split("\n\n", 1)[0]
    import re

    selectors = re.findall(
        r"((?:backend|scripts)/[^\s\\]+\.py)::(test_[A-Za-z0-9_]+)", target
    )
    assert selectors, (
        "test-real-lifecycle must retain explicit production-shaped pytest nodes"
    )
    for path_text, test_name in selectors:
        path = ROOT / path_text
        assert path.is_file(), (
            f"test-real-lifecycle references missing file: {path_text}"
        )
        assert test_name in _functions(path), (path_text, test_name)


def test_real_lifecycle_makefile_selectors_are_declared_in_ledger() -> None:
    import re

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\ntest-real-lifecycle:\n", 1)[1].split("\n\n", 1)[0]
    selectors = {
        f"{path_text}::{test_name}"
        for path_text, test_name in re.findall(
            r"((?:backend|scripts)/[^\s\\]+\.py)::(test_[A-Za-z0-9_]+)", target
        )
    }
    payload = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    declared = {
        proof["test"]
        for row in payload["lifecycles"]
        for proof in row.get("proofs", [])
        if "test" in proof
    }
    assert selectors <= declared, (
        "test-real-lifecycle contains executable proofs missing from REAL_LIFECYCLE_QA.yml",
        sorted(selectors - declared),
    )


def test_real_lifecycle_ledger_pytest_proofs_are_selected_by_make_target() -> None:
    import re

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\ntest-real-lifecycle:\n", 1)[1].split("\n\n", 1)[0]
    selected_files = {
        path_text
        for path_text in re.findall(r"((?:backend|scripts)/[^\s\\]+\.py)(?!::)", target)
    }
    selected_nodes = {
        f"{path_text}::{test_name}"
        for path_text, test_name in re.findall(
            r"((?:backend|scripts)/[^\s\\]+\.py)::(test_[A-Za-z0-9_]+)", target
        )
    }
    payload = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    missing: list[str] = []
    for row in payload["lifecycles"]:
        if row.get("external_gate"):
            continue
        for proof in row.get("proofs", []):
            test = proof.get("test")
            if not test:
                continue
            path_text = test.split("::", 1)[0]
            if test not in selected_nodes and path_text not in selected_files:
                missing.append(test)
    assert not missing, (
        "REAL_LIFECYCLE_QA.yml claims pytest evidence not selected by test-real-lifecycle",
        sorted(missing),
    )
