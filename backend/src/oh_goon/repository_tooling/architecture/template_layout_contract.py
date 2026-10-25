from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

from oh_goon.core.errors import FleetPlanSourceNotFound
from oh_goon.core.source_transport_metadata import authored_source_entries
from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import (
    BACKEND_ROOT,
    GOON_TEMPLATES_ROOT,
    PLAN_TEMPLATES_ROOT,
    TEMPLATES_ROOT,
)
from oh_goon.runtime.template_discovery import validate_template_package

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def check_template_layout_contract() -> list[Violation]:
    violations: list[Violation] = []
    if {p.name for p in authored_source_entries(TEMPLATES_ROOT)} != {"README.md", "goons", "plans"}:
        violations.append(
            Violation(TEMPLATES_ROOT, "templates/ must contain only README.md, goons/, and plans/")
        )
    _check_root(GOON_TEMPLATES_ROOT, "goon.yml", "Goon", violations)
    _check_root(PLAN_TEMPLATES_ROOT, "plan.yml", "Plan", violations)
    _check_wheel_force_include(BACKEND_ROOT / "pyproject.toml", violations)

    readme = (TEMPLATES_ROOT / "README.md").read_text(encoding="utf-8")
    for token in ("Composite Goon", "README.md", "run:"):
        if token not in readme:
            violations.append(
                Violation(TEMPLATES_ROOT / "README.md", f"template start page missing {token!r}")
            )
    return violations


def _check_wheel_force_include(pyproject_path: Path, violations: list[Violation]) -> None:
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        violations.append(
            Violation(pyproject_path, f"backend pyproject.toml must be valid TOML: {exc}")
        )
        return

    force_include = (
        pyproject.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("force-include", {})
    )
    if not isinstance(force_include, dict):
        force_include = {}
    for source in ("../templates/goons", "../templates/plans"):
        if source in force_include:
            violations.append(
                Violation(
                    pyproject_path,
                    f'wheel packaging must stage "{source}" through the release owner',
                )
            )
    custom_hook = (
        pyproject.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("hooks", {})
        .get("custom", {})
    )
    if not isinstance(custom_hook, dict) or custom_hook.get("path") != "hatch_build.py":
        violations.append(
            Violation(pyproject_path, "wheel packaging requires its admitted-source custom hook")
        )


def _check_root(root: Path, source_name: str, kind: str, violations: list[Violation]) -> None:
    if (root / "catalog.yml").exists():
        violations.append(
            Violation(root / "catalog.yml", "template discovery must not use authored catalog.yml")
        )
    for category in authored_source_entries(root):
        if category.name == "README.md":
            continue
        if not category.is_dir() or category.is_symlink() or _NAME.fullmatch(category.name) is None:
            violations.append(
                Violation(
                    category,
                    "template root may contain only canonical category directories and README.md",
                )
            )
            continue
        for package in authored_source_entries(category):
            if (
                not package.is_dir()
                or package.is_symlink()
                or _NAME.fullmatch(package.name) is None
            ):
                violations.append(
                    Violation(
                        package, "template category may contain only canonical package directories"
                    )
                )
                continue
            try:
                source = validate_template_package(package, source_name=source_name, kind=kind)
            except FleetPlanSourceNotFound as exc:
                violations.append(Violation(package, str(exc)))
                continue
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                violations.append(Violation(source, f"{kind} YAML root must be a mapping"))
                continue
            canonical = f"{category.name}/{package.name}"
            if (
                not (package / "README.md")
                .read_text(encoding="utf-8")
                .startswith(f"# {canonical}\n")
            ):
                violations.append(
                    Violation(
                        package / "README.md",
                        f"template README must start with canonical heading '# {canonical}'",
                    )
                )
