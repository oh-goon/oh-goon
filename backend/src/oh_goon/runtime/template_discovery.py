from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceNotFound
from oh_goon.core.source_transport_metadata import authored_source_entries
from oh_goon.core.template_identity import (
    canonical_template_key,
    template_key_parts,
    template_segment,
)


def discover_template_packages(root: Path, *, source_name: str, kind: str) -> dict[str, Path]:
    """Discover exact type/category/name packages without recursive lookup or aliases."""

    if not root.is_dir() or root.is_symlink():
        raise FleetPlanSourceNotFound(f"{kind} template root does not exist")

    packages: dict[str, Path] = {}
    for category in authored_source_entries(root):
        if category.name == "README.md":
            if not category.is_file() or category.is_symlink():
                raise FleetPlanSourceNotFound(
                    f"{kind} template root README.md must be a regular file"
                )
            continue
        if not category.is_dir() or category.is_symlink():
            raise FleetPlanSourceNotFound(
                f"{kind} template root may contain only README.md and category directories"
            )
        try:
            category_name = template_segment(category.name, kind=f"{kind} category")
        except FleetPlanInvalid as exc:
            raise FleetPlanSourceNotFound(str(exc)) from exc
        for directory in authored_source_entries(category):
            if not directory.is_dir() or directory.is_symlink():
                raise FleetPlanSourceNotFound(
                    f"{kind} category {category.name} may contain only template package directories"
                )
            try:
                package_name = template_segment(directory.name, kind=f"{kind} package")
            except FleetPlanInvalid as exc:
                raise FleetPlanSourceNotFound(str(exc)) from exc
            validate_template_package(directory, source_name=source_name, kind=kind)
            key = f"{category_name}/{package_name}"
            packages[key] = directory
    return dict(sorted(packages.items()))


def discover_template_keys(root: Path, *, source_name: str, kind: str) -> tuple[str, ...]:
    return tuple(discover_template_packages(root, source_name=source_name, kind=kind))


def template_package(
    root: Path, template_key: str, *, source_name: str, kind: str
) -> tuple[str, Path]:
    """Map one logical import to exactly root/category/name/source_name."""

    try:
        key = canonical_template_key(template_key, kind=kind)
        category, name = template_key_parts(key, kind=kind)
    except FleetPlanInvalid as exc:
        raise FleetPlanSourceNotFound(str(exc)) from exc
    if not root.is_dir() or root.is_symlink():
        raise FleetPlanSourceNotFound(f"{kind} template root does not exist")
    directory = root / category / name
    if not directory.is_dir() or directory.is_symlink():
        raise FleetPlanSourceNotFound(f"{kind} template does not exist: {key}")
    validate_template_package(directory, source_name=source_name, kind=kind)
    return key, directory


def validate_template_package(directory: Path, *, source_name: str, kind: str) -> Path:
    expected = {source_name, "README.md"}
    entries = {entry.name for entry in authored_source_entries(directory)}
    if entries != expected:
        missing = sorted(expected - entries)
        extra = sorted(entries - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise FleetPlanSourceNotFound(
            f"{kind} template {directory.name} must contain exactly {source_name} and README.md"
            + (f" ({'; '.join(details)})" if details else "")
        )
    for filename in expected:
        candidate = directory / filename
        if not candidate.is_file() or candidate.is_symlink():
            raise FleetPlanSourceNotFound(
                f"{kind} template {directory.name} requires a regular non-symlink {filename}"
            )
    return directory / source_name


def template_readme_description(directory: Path, *, kind: str) -> str:
    """Return the first prose paragraph from the package README.

    Human-facing description belongs to README.md; execution YAML stays structural.
    """

    readme = directory / "README.md"
    try:
        lines = readme.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FleetPlanSourceNotFound(
            f"{kind} template {directory.name} README.md is unreadable"
        ) from exc

    paragraph: list[str] = []
    heading = ""
    started = False
    for raw in lines:
        line = raw.strip()
        if not started:
            if not line:
                continue
            if line.startswith("#"):
                if not heading:
                    heading = line.lstrip("#").strip()
                continue
            started = True
            paragraph.append(line)
            continue
        if not line or line.startswith("#"):
            break
        paragraph.append(line)

    description = " ".join(paragraph).strip() or heading
    if description.startswith("**") and description.endswith("**") and len(description) > 4:
        description = description[2:-2].strip()
    if not description:
        raise FleetPlanSourceNotFound(
            f"{kind} template {directory.name} README.md requires a human-readable title or description"
        )
    if len(description) > 1000:
        description = description[:997].rstrip() + "..."
    return description
