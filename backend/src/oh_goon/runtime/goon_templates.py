from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.shared_plan import (
    GoonTemplateCatalog,
    GoonTemplateDocument,
    GoonTemplateSummary,
)
from oh_goon.core.template_identity import template_classification
from oh_goon.integrations.fleet_plan_source_snapshot import (
    FleetPlanSourceSnapshot,
    read_fleet_plan_source,
)
from oh_goon.integrations.canonical_goon_tool_authority import (
    apply_canonical_goon_tool_authority,
    load_canonical_goon_tool_authority,
)
from oh_goon.runtime.template_discovery import (
    discover_template_packages,
    template_package,
    validate_template_package,
    template_readme_description,
)


def default_goon_templates_root() -> Path:
    """Return the checked-in template root in source, or its wheel-packaged location."""
    package_root = Path(__file__).resolve().parents[1]
    packaged = package_root / "templates" / "goons"
    source_tree = Path(__file__).resolve().parents[4] / "templates" / "goons"
    if packaged.is_dir():
        return packaged
    return source_tree


class GoonTemplateStore:
    """Read-only canonical Goon packages discovered at exact category/package depth."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        tool_authority_path: Path | None = None,
    ) -> None:
        self.root = (root or default_goon_templates_root()).resolve()
        if tool_authority_path is None:
            tool_authority_path = self._discover_tool_authority_path()
        self._tool_authority = (
            load_canonical_goon_tool_authority(tool_authority_path)
            if tool_authority_path is not None
            else {}
        )

    def _discover_tool_authority_path(self) -> Path | None:
        packaged = self.root.parent.parent / "canonical_goon_tool_authority.json"
        if packaged.is_file():
            return packaged
        source = (
            self.root.parent.parent
            / "backend"
            / "src"
            / "oh_goon"
            / "canonical_goon_tool_authority.json"
        )
        return source if source.is_file() else None

    def catalog(self) -> GoonTemplateCatalog:
        packages = self._packages()
        return GoonTemplateCatalog(
            templates=tuple(self._summary(key, directory) for key, directory in packages.items())
        )

    def keys(self) -> tuple[str, ...]:
        return tuple(self._packages())

    def read(self, template_key: str) -> GoonTemplateDocument:
        key, directory = template_package(
            self.root, template_key, source_name="goon.yml", kind="Goon"
        )
        source = self._source(key, directory)
        relative_source = source.path.relative_to(self.root).as_posix()
        return GoonTemplateDocument(
            template_key=key,
            classification=template_classification(key, kind="Goon"),
            description=template_readme_description(directory, kind="Goon"),
            template_path=f"templates/goons/{relative_source}",
            sha256=source.sha256,
            content=source.content,
        )

    def source(self, template_key: str) -> FleetPlanSourceSnapshot:
        key, directory = template_package(
            self.root, template_key, source_name="goon.yml", kind="Goon"
        )
        return self._source(key, directory)

    def _packages(self) -> dict[str, Path]:
        return discover_template_packages(self.root, source_name="goon.yml", kind="Goon")

    def _source(self, key: str, directory: Path) -> FleetPlanSourceSnapshot:
        path = validate_template_package(directory, source_name="goon.yml", kind="Goon")
        snapshot = read_fleet_plan_source(
            path,
            kind="goon",
            source=f"template:{key}/goon.yml",
            expected_name=directory.name,
        )
        compiled = apply_canonical_goon_tool_authority(
            snapshot.compiled,
            template_key=key,
            authority=self._tool_authority,
        )
        return FleetPlanSourceSnapshot(
            path=snapshot.path,
            content=snapshot.content,
            sha256=snapshot.sha256,
            compiled=compiled,
            step_positions=snapshot.step_positions,
        )

    def _summary(self, key: str, directory: Path) -> GoonTemplateSummary:
        source = self._source(key, directory)
        relative_source = source.path.relative_to(self.root).as_posix()
        return GoonTemplateSummary(
            template_key=key,
            classification=template_classification(key, kind="Goon"),
            description=template_readme_description(directory, kind="Goon"),
            template_path=f"templates/goons/{relative_source}",
            sha256=source.sha256,
        )
