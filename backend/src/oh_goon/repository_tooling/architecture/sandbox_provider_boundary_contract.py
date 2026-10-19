from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

_TRANSPORT = Path("backend/src/oh_goon/integrations/_fleet_plan_opensandbox_transport.py")
_ISOLATION = Path("backend/src/oh_goon/integrations/fleet_plan_opensandbox_isolation.py")
_PYPROJECT = Path("backend/pyproject.toml")


def _imports_opensandbox(path: Path) -> bool:
    try:
        tree = python_ast(path)
    except (OSError, SyntaxError):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "opensandbox" or alias.name.startswith("opensandbox.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "opensandbox" or (node.module or "").startswith("opensandbox."):
                return True
    return False


def _assigns_environment_key(path: Path, key: str) -> bool:
    if not path.is_file():
        return False
    try:
        tree = python_ast(path)
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Subscript):
                continue
            index = target.slice
            if isinstance(index, ast.Constant) and index.value == key:
                return True
    return False


def check_sandbox_provider_boundary_contract(root: Path = ROOT) -> list[Violation]:
    violations: list[Violation] = []
    transport = root / _TRANSPORT
    isolation = root / _ISOLATION
    pyproject = root / _PYPROJECT

    if _imports_opensandbox(transport):
        violations.append(
            Violation(
                transport, "OpenSandbox provider transport must not import the provider Python SDK"
            )
        )

    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        violations.append(Violation(pyproject, "backend pyproject must remain parseable"))
    else:
        dependencies = list(project.get("project", {}).get("dependencies", ()))
        optional = project.get("project", {}).get("optional-dependencies", {})
        all_declared = dependencies + [item for values in optional.values() for item in values]
        if any(
            str(item).split("[", 1)[0].split("=", 1)[0].strip().lower() == "opensandbox"
            for item in all_declared
        ):
            violations.append(
                Violation(
                    pyproject,
                    "OpenSandbox provider must not become Oh-Goon Python dependency authority",
                )
            )

    isolation_text = isolation.read_text(encoding="utf-8") if isolation.is_file() else ""
    for token in (
        "runtime_attestation_sha256",
        "osb_sha256",
        "OpenSandboxRuntimeKind.KATA",
        "OpenSandboxRuntimeKind.FIRECRACKER",
        "server_configuration_sha256",
    ):
        if token not in isolation_text:
            violations.append(
                Violation(isolation, f"sandbox provider authority contract is missing: {token}")
            )
    if (
        "OpenSandboxRuntimeKind.RUNC," in isolation_text
        or "OpenSandboxRuntimeKind.GVISOR," in isolation_text
    ):
        violations.append(
            Violation(
                isolation, "deny-all remote OpenSandbox backend must not admit runc or gVisor"
            )
        )

    transport_text = transport.read_text(encoding="utf-8") if transport.is_file() else ""
    for token in ('"resourceLimits"',):
        if token not in transport_text:
            violations.append(
                Violation(
                    transport,
                    f"sandbox provider transport is missing fail-closed public field: {token}",
                )
            )
    if not _assigns_environment_key(transport, "OPEN_SANDBOX_API_KEY"):
        violations.append(
            Violation(
                transport,
                "OpenSandbox API key must be transported through environment authority, independent of private local names",
            )
        )

    return violations
