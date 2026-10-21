from __future__ import annotations

import ast
import importlib.metadata
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from oh_goon.repository_tooling import hosted_dependency_capability as capability
from oh_goon.repository_tooling import hosted_diagnostic_receipt as receipt

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "backend/src/oh_goon"
OWNER_PATH = SOURCE_ROOT / "repository_tooling/hosted_dependency_capability.py"
VERIFIER_PATH = SOURCE_ROOT / "repository_tooling/hosted_diagnostic_receipt.py"


def _versions(**overrides: str) -> Callable[[str], str]:
    versions = dict(overrides)

    def read(name: str) -> str:
        value = versions.get(name, "99.0.0")
        if value == "MISSING":
            raise importlib.metadata.PackageNotFoundError(name)
        return value

    return read


def _no_op_import_probe(_distribution: str, _import_name: str) -> None:
    return None


def _payload() -> dict[str, object]:
    return capability.build_capability(
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        interpreter=str(Path(sys.executable).resolve()),
        python_version="3.13.5",
    )


def _resign(payload: dict[str, object]) -> None:
    payload["identity"] = receipt._canonical_identity(payload)


def test_schema_owner_mutation_flows_to_producer_and_verifier_without_verifier_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutated_schema = "oh-goon.hosted-dependency-capability/v3"
    verifier_before = VERIFIER_PATH.read_bytes()
    monkeypatch.setattr(capability, "SCHEMA", mutated_schema)

    payload = _payload()

    assert payload["schema"] == mutated_schema
    receipt._validate_host_capability_header(payload)
    receipt._validate_host_capability_packages(payload)
    assert VERIFIER_PATH.read_bytes() == verifier_before


def test_verifier_rejects_stale_schema_after_owner_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    monkeypatch.setattr(capability, "SCHEMA", "oh-goon.hosted-dependency-capability/v3")

    with pytest.raises(
        RuntimeError, match="unsupported hosted dependency capability schema"
    ):
        receipt._validate_host_capability_header(payload)


def test_verifier_rejects_unknown_schema_even_with_valid_payload_identity() -> None:
    payload = _payload()
    payload["schema"] = "oh-goon.hosted-dependency-capability/unknown"
    _resign(payload)

    with pytest.raises(
        RuntimeError, match="unsupported hosted dependency capability schema"
    ):
        receipt._validate_host_capability_header(payload)


def test_payload_identity_tampering_remains_independently_rejected() -> None:
    payload = _payload()
    payload["dependency_intent_sha256"] = "sha256:" + "0" * 64

    with pytest.raises(RuntimeError, match="identity does not match payload"):
        receipt._validate_host_capability_header(payload)


def test_interpreter_binding_remains_independently_rejected() -> None:
    payload = _payload()
    payload["interpreter"] = "/different/python"
    _resign(payload)

    with pytest.raises(RuntimeError, match="interpreter does not match receipt writer"):
        receipt._validate_host_capability_header(payload)


def test_non_authoritative_status_remains_independently_rejected() -> None:
    payload = _payload()
    payload["native_authority"] = True
    _resign(payload)

    with pytest.raises(RuntimeError, match="attempted to claim native authority"):
        receipt._validate_host_capability_header(payload)


def test_capability_evidence_tampering_remains_independently_rejected() -> None:
    payload = _payload()
    packages = payload["packages"]
    assert isinstance(packages, list)
    crypto = next(
        row
        for row in packages
        if isinstance(row, dict) and row.get("distribution") == "cryptography"
    )
    assert isinstance(crypto, dict)
    crypto["status"] = "missing"
    _resign(payload)

    receipt._validate_host_capability_header(payload)
    with pytest.raises(
        RuntimeError, match="no compatible required capability proof for cryptography"
    ):
        receipt._validate_host_capability_packages(payload)


def test_production_has_one_hosted_dependency_capability_schema_owner() -> None:
    schema_literal = "oh-goon.hosted-dependency-capability/v2"
    literal_owners: list[Path] = []
    legacy_assignments: list[tuple[Path, int]] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == schema_literal:
                literal_owners.append(path)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if any(
                    isinstance(target, ast.Name)
                    and target.id == "HOST_CAPABILITY_SCHEMA"
                    for target in targets
                ):
                    legacy_assignments.append((path, node.lineno))

    assert literal_owners == [OWNER_PATH]
    assert legacy_assignments == []
    assert not hasattr(receipt, "HOST_CAPABILITY_SCHEMA")
