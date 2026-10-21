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
OWNER_PATH = (
    ROOT / "backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py"
)
HOSTED_ORCHESTRATORS = (
    ROOT / "scripts/backend_core_test_diagnostic.sh",
    ROOT / "scripts/development_test.sh",
)


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


def test_owner_mutation_flows_through_probe_payload_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capability, "REQUIRED_HOST_CAPABILITIES", ("pytest",))

    blockers = capability.required_host_capability_blockers(
        version_reader=_versions(cryptography="MISSING"),
        import_probe=_no_op_import_probe,
    )
    assert blockers == ()

    payload = capability.build_capability(
        version_reader=_versions(cryptography="MISSING"),
        import_probe=_no_op_import_probe,
        interpreter=str(Path(sys.executable).resolve()),
        python_version="3.13.5",
    )
    assert payload["required_host_capabilities"] == ["pytest"]
    assert payload["result"] == "HOST-CAPABILITY-READY"

    receipt._validate_host_capability_header(payload)
    receipt._validate_host_capability_packages(payload)


def test_receipt_rejects_reordered_or_extra_required_capability_projection() -> None:
    payload = capability.build_capability(
        version_reader=_versions(),
        import_probe=_no_op_import_probe,
        interpreter=str(Path(sys.executable).resolve()),
        python_version="3.13.5",
    )
    payload["required_host_capabilities"] = list(
        reversed(capability.REQUIRED_HOST_CAPABILITIES)
    )
    payload["identity"] = receipt._canonical_identity(payload)

    with pytest.raises(RuntimeError, match="required capability contract"):
        receipt._validate_host_capability_header(payload)


def test_required_capability_semantics_have_one_authored_assignment() -> None:
    assignments: list[Path] = []
    duplicate_literals: list[Path] = []
    required = set(capability.REQUIRED_HOST_CAPABILITIES)

    for path in (ROOT / "backend/src/oh_goon").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if any(
                    isinstance(target, ast.Name)
                    and target.id == "REQUIRED_HOST_CAPABILITIES"
                    for target in node.targets
                ):
                    assignments.append(path)
            elif isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "REQUIRED_HOST_CAPABILITIES"
                ):
                    assignments.append(path)
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                values = {
                    item.value
                    for item in node.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
                if values == required and len(node.elts) == len(required):
                    duplicate_literals.append(path)

    assert assignments == [OWNER_PATH]
    assert duplicate_literals == [OWNER_PATH]


def test_hosted_orchestrators_do_not_privately_probe_required_package_names() -> None:
    for path in HOSTED_ORCHESTRATORS:
        text = path.read_text(encoding="utf-8")
        assert "-c 'import pytest'" not in text
        assert '-c "import pytest"' not in text
