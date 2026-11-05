from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    python_ast,
    python_semantic_facts,
)


def _direct_dependency_lock_content_parsing(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(module == "tomllib" for module in modules):
                return True
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "open",
            "read_bytes",
            "read_text",
        }:
            return True
    return False


def _check_lock_authority(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    authoring = root / "backend/src/oh_goon/repository_tooling/dependency_lock_authoring.py"
    if authoring.exists():
        violations.append(
            Violation(authoring, "dependency lock authoring must be owned directly by uv/npm")
        )
    checks = root / "backend/src/oh_goon/repository_tooling/dependency_locks.py"
    if not checks.is_file():
        return violations
    tree = python_ast(checks)
    if _direct_dependency_lock_content_parsing(tree):
        violations.append(
            Violation(
                checks,
                "dependency validation must not read or parse lock contents in parallel with native uv/npm authority",
            )
        )
    ignore = root / ".gitignore"
    if ignore.is_file() and "backend/uv.lock" in ignore.read_text(encoding="utf-8").splitlines():
        violations.append(Violation(ignore, "committed backend/uv.lock must not be ignored"))
    return violations


def _check_dependency_privacy(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    privacy_module = "oh_goon.repository_tooling.public_dependency_privacy"
    boundary = root / "backend/src/oh_goon/repository_tooling/public_dependency_privacy.py"
    if not boundary.is_file():
        violations.append(
            Violation(
                boundary, "public/private dependency privacy boundary must be machine-enforced"
            )
        )
        return violations

    consumers = (
        root / "backend/src/oh_goon/repository_tooling/dependency_locks.py",
        root / "backend/src/oh_goon/repository_tooling/release/package_slim.py",
        root / "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
        root / "backend/src/oh_goon/repository_tooling/release/accept_container.py",
    )
    for consumer in consumers:
        if not consumer.is_file():
            violations.append(
                Violation(
                    consumer,
                    "dependency privacy semantic consumer is missing",
                )
            )
            continue
        tree = python_ast(consumer)
        consumes_privacy_owner = any(
            (isinstance(node, ast.ImportFrom) and node.module == privacy_module)
            or (
                isinstance(node, ast.Import)
                and any(alias.name == privacy_module for alias in node.names)
            )
            for node in ast.walk(tree)
        )
        if not consumes_privacy_owner:
            violations.append(
                Violation(
                    consumer,
                    "dependency privacy surface must consume the public dependency privacy semantic owner",
                )
            )
    return violations


def _check_native_command_policy(root: Path) -> list[Violation]:
    native = root / "backend/src/oh_goon/repository_tooling/native_command.py"
    if not native.is_file():
        return [Violation(native, "native command observer is missing")]
    tree = python_ast(native)
    text = native.read_text(encoding="utf-8")
    violations: list[Violation] = []

    inherits_environment = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "copy"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
        and node.func.value.attr == "environ"
        for node in ast.walk(tree)
    )
    if not inherits_environment:
        violations.append(
            Violation(
                native,
                "native command observer must derive child environment from os.environ without depending on a private local name",
            )
        )

    for key in ("UV_DEFAULT_INDEX", "UV_INDEX", "UV_CONFIG_FILE"):
        if key not in text:
            violations.append(
                Violation(
                    native,
                    f"native command observer must preserve operator/native uv environment evidence for {key}",
                )
            )

    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "time"
        and node.func.attr == "sleep"
        for node in ast.walk(tree)
    ):
        violations.append(Violation(native, "Oh-Goon must not implement native-tool retry sleeps"))

    forbidden_popped = {
        "UV_SYSTEM_CERTS",
        "UV_DEFAULT_INDEX",
        "UV_INDEX",
        "UV_CONFIG_FILE",
        "HOME",
        "XDG_CONFIG_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "pop"
            and node.args
        ):
            continue
        popped_key = node.args[0]
        if (
            isinstance(popped_key, ast.Constant)
            and isinstance(popped_key.value, str)
            and popped_key.value in forbidden_popped
        ):
            violations.append(
                Violation(
                    native,
                    f"certification must inherit operator/native environment instead of removing {popped_key.value}",
                )
            )
    for token in ("source ~/.bashrc", ". ~/.bashrc"):
        if token in text:
            violations.append(
                Violation(
                    native,
                    "certification must not source shell startup files to manufacture native uv configuration",
                )
            )
    return violations


def _certification_goon_network_violation(goon: Path) -> Violation | None:
    if "host_unrestricted" not in goon.read_text(encoding="utf-8"):
        return None
    return Violation(
        goon,
        "canonical Oh-Goon verification/release Goons must not acquire host-unrestricted egress implicitly; network authority belongs to an explicitly justified semantic owner",
    )


def _check_certification_goon_network(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    goon_root = root / "templates/goons/oh-goon"
    if goon_root.is_dir():
        for goon in goon_root.glob("*/goon.yml"):
            violation = _certification_goon_network_violation(goon)
            if violation is not None:
                violations.append(violation)
    return violations


def _check_native_authority_deduplication(root: Path) -> list[Violation]:
    """Keep native-tool decisions single-owned across release/diagnostic paths."""

    violations: list[Violation] = []
    accept_release = root / "backend/src/oh_goon/repository_tooling/release/accept_release.py"
    if accept_release.is_file():
        facts = python_semantic_facts(accept_release)
        imports_lock_owner = any(
            imported == "oh_goon.repository_tooling.dependency_locks"
            or imported.startswith("oh_goon.repository_tooling.dependency_locks.")
            for imported in facts.imports
        )
        if imports_lock_owner:
            violations.append(
                Violation(
                    accept_release,
                    "release acceptance must delegate dependency admissibility to build-release instead of re-probing or reinterpreting uv/npm locks",
                )
            )

    release = root / "scripts/release/build_release.sh"
    if release.is_file():
        text = release.read_text(encoding="utf-8")
        if text.count("make release-locks-check") != 1:
            violations.append(
                Violation(
                    release,
                    "release assembly must invoke exactly one pre-materialization release-locks-check owner",
                )
            )
        for token in ("uv lock --project backend --check", "npm ci --package-lock-only"):
            if token in text:
                violations.append(
                    Violation(
                        release,
                        f"release assembly must not duplicate release-locks-check native semantics: {token}",
                    )
                )

    return violations


def check_project_dependency_contract(root: Path) -> list[Violation]:
    return [
        *_check_lock_authority(root),
        *_check_dependency_privacy(root),
        *_check_native_command_policy(root),
        *_check_certification_goon_network(root),
        *_check_native_authority_deduplication(root),
    ]
