from __future__ import annotations

import ast
import json
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.typescript_semantics import project_typescript

_TOOL_MARKERS = ("agentscookbook", "agent_economics", "agent-economics")
_REQUIRED_IGNORES = (".agents-cookbook/", ".agent-economics/")
_SOURCE_RECORD = Path("docs/development/agents-cookbook-source.json")
_BOUNDARY_DOC = Path("docs/development/AGENTS_COOKBOOK_STRUCTURE_DOGFOOD.md")


def _contains_tool_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _TOOL_MARKERS)


def _is_tool_module(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    roots = ("agentscookbook", "agent_economics")
    return any(normalized == root or normalized.startswith(f"{root}.") for root in roots)


def _imported_module_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return (node.module,)
    return ()


def _dynamic_import_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    function = node.func
    if isinstance(function, ast.Name) and function.id == "__import__":
        return first.value
    if isinstance(function, ast.Attribute) and function.attr == "import_module":
        return first.value
    return None


def _python_imports_external_tool(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        names = (*_imported_module_names(node), *filter(None, (_dynamic_import_name(node),)))
        if any(_is_tool_module(name) for name in names):
            return True
    return False


def _frontend_imports_external_tool(text: str) -> bool:
    return any(_contains_tool_marker(name) for name in project_typescript(text).module_imports)


def _active_text_references_tool(text: str) -> bool:
    """Detect executable/config references, not comment-only mentions."""

    active = (
        raw_line.strip()
        for raw_line in text.splitlines()
        if raw_line.strip() and not raw_line.lstrip().startswith(("#", "//", ";"))
    )
    return any(_contains_tool_marker(line) for line in active)


def _runtime_file_leaks(path: Path, kind: str, text: str) -> bool:
    if kind == "python":
        return path.suffix == ".py" and _python_imports_external_tool(text)
    if kind == "frontend":
        return path.suffix in {".js", ".jsx", ".ts", ".tsx"} and _frontend_imports_external_tool(
            text
        )
    return kind == "config" and _active_text_references_tool(text)


def _runtime_files(tree: Path, *, skip_repository_tooling: bool) -> Iterable[Path]:
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if skip_repository_tooling and "repository_tooling" in path.relative_to(tree).parts:
            continue
        yield path


def _scan_one_runtime_tree(
    tree: Path, kind: str, *, skip_repository_tooling: bool = False
) -> list[Violation]:
    if not tree.is_dir():
        return []
    violations: list[Violation] = []
    for path in _runtime_files(tree, skip_repository_tooling=skip_repository_tooling):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _runtime_file_leaks(path, kind, text):
            violations.append(
                Violation(
                    path,
                    "agentsCookbook/Agent Economics is developer-only and must not enter runtime, template, or container source",
                )
            )
    return violations


def _scan_runtime_tree(root: Path) -> list[Violation]:
    return [
        *_scan_one_runtime_tree(
            root / "backend/src/oh_goon", "python", skip_repository_tooling=True
        ),
        *_scan_one_runtime_tree(root / "frontend/src", "frontend"),
        *_scan_one_runtime_tree(root / "templates", "config"),
        *_scan_one_runtime_tree(root / "docker", "config"),
    ]


def _dependency_children(value: Any) -> tuple[Any, ...]:
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, dict):
        return (*value.keys(), *value.values())
    return ()


def _iter_dependency_strings(value: Any) -> Iterable[str]:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
            continue
        stack.extend(_dependency_children(item))


def _contains_dependency_reference(*surfaces: Any) -> bool:
    return any(
        _contains_tool_marker(item)
        for surface in surfaces
        for item in _iter_dependency_strings(surface)
    )


def _pyproject_dependency_references(path: Path) -> bool:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    project = payload.get("project", {})
    return _contains_dependency_reference(
        project.get("dependencies", []),
        project.get("optional-dependencies", {}),
        payload.get("build-system", {}).get("requires", []),
        payload.get("dependency-groups", {}),
    )


def _package_json_dependency_references(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _contains_dependency_reference(
        *(
            payload.get(name, {})
            for name in (
                "dependencies",
                "devDependencies",
                "peerDependencies",
                "optionalDependencies",
            )
        )
    )


def _package_lock_dependency_references(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    packages = payload.get("packages", {})
    if isinstance(packages, dict) and any(_contains_tool_marker(str(name)) for name in packages):
        return True
    package_dependency_maps = []
    if isinstance(packages, dict):
        package_dependency_maps = [
            package.get(name, {})
            for package in packages.values()
            if isinstance(package, dict)
            for name in (
                "dependencies",
                "devDependencies",
                "peerDependencies",
                "optionalDependencies",
            )
        ]
    return _contains_dependency_reference(payload.get("dependencies", {}), package_dependency_maps)


def _scan_dependency_manifests(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    checks = (
        (root / "backend/pyproject.toml", _pyproject_dependency_references),
        (root / "frontend/package.json", _package_json_dependency_references),
        (root / "frontend/package-lock.json", _package_lock_dependency_references),
    )
    for path, check in checks:
        if not path.is_file():
            continue
        try:
            leaked = check(path)
        except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            violations.append(Violation(path, f"cannot inspect dependency manifest: {exc}"))
            continue
        if leaked:
            violations.append(
                Violation(
                    path,
                    "agentsCookbook/Agent Economics must not be a product or package dependency",
                )
            )
    return violations


def _check_source_record(root: Path) -> list[Violation]:
    path = root / _SOURCE_RECORD
    if not path.is_file():
        return [Violation(path, "external developer-tool source record is required")]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Violation(path, f"external developer-tool source record must be valid JSON: {exc}")]
    violations: list[Violation] = []
    commit = payload.get("commit")
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(ch not in "0123456789abcdef" for ch in commit)
    ):
        violations.append(
            Violation(path, "agentsCookbook source must bind one full lowercase Git commit SHA")
        )
    for field in (
        "production_dependency",
        "runtime_authority",
        "certification_authority",
        "repository_intelligence_authority",
    ):
        if payload.get(field) is not False:
            violations.append(Violation(path, f"{field} must remain explicitly false"))
    if payload.get("tool") != "agentsCookbook":
        violations.append(Violation(path, "source record must identify agentsCookbook exactly"))
    return violations


def _check_ignored_checkouts(root: Path) -> list[Violation]:
    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    return [
        Violation(gitignore, f"disposable external developer checkout must stay ignored: {marker}")
        for marker in _REQUIRED_IGNORES
        if marker not in text
    ]


def _check_boundary_document(root: Path) -> list[Violation]:
    boundary_doc = root / _BOUNDARY_DOC
    if not boundary_doc.is_file():
        return [Violation(boundary_doc, "agentsCookbook dogfood boundary document is required")]
    boundary_text = boundary_doc.read_text(encoding="utf-8")
    required = (
        "external development-only aid",
        "Do **not** carry a previous hotspot or filename forward as a predetermined target",
        "Hashmarks",
        "Oh-Goon's own verification and certification gates remain authoritative",
    )
    return [
        Violation(boundary_doc, f"missing required dogfood boundary marker: {marker}")
        for marker in required
        if marker not in boundary_text
    ]


def _check_vendor_boundary(root: Path) -> list[Violation]:
    vendor = root / "vendor"
    if not vendor.is_dir():
        return []
    return [
        Violation(path, "agentsCookbook must not be vendored as Oh-Goon supply")
        for path in vendor.iterdir()
        if _contains_tool_marker(path.name)
    ]


def check_external_development_tooling_contract(root: Path = ROOT) -> list[Violation]:
    """Keep external refactor tooling outside Oh-Goon product/runtime authority."""

    return [
        *_scan_dependency_manifests(root),
        *_scan_runtime_tree(root),
        *_check_source_record(root),
        *_check_ignored_checkouts(root),
        *_check_boundary_document(root),
        *_check_vendor_boundary(root),
    ]
