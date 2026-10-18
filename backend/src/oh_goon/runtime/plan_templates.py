from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.plan_template import (
    PlanTemplateCatalog,
    PlanTemplateDocument,
    PlanTemplateSummary,
)
from oh_goon.core.template_identity import template_classification
from oh_goon.integrations.fleet_plan_source_snapshot import (
    FleetPlanSourceSnapshot,
    read_fleet_plan_source,
)
from oh_goon.runtime.template_discovery import (
    discover_template_packages,
    template_package,
    template_readme_description,
    validate_template_package,
)


def default_plan_templates_root() -> Path:
    """Return the checked-in plan template root in source, or its wheel-packaged location."""
    package_root = Path(__file__).resolve().parents[1]
    packaged = package_root / "templates" / "plans"
    source_tree = Path(__file__).resolve().parents[4] / "templates" / "plans"
    if packaged.is_dir():
        return packaged
    return source_tree


class PlanTemplateStore:
    """Read-only shipped Plan packages addressed by canonical package name."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_plan_templates_root()).resolve()

    def catalog(self) -> PlanTemplateCatalog:
        packages = self._packages()
        return PlanTemplateCatalog(
            templates=tuple(self._summary(key, directory) for key, directory in packages.items())
        )

    def keys(self) -> tuple[str, ...]:
        return tuple(self._packages())

    def read(self, template_key: str) -> PlanTemplateDocument:
        key, directory = template_package(
            self.root, template_key, source_name="plan.yml", kind="Plan"
        )
        source = self._source(key, directory)
        relative_source = source.path.relative_to(self.root).as_posix()
        return PlanTemplateDocument(
            template_key=key,
            classification=template_classification(key, kind="Plan"),
            description=template_readme_description(directory, kind="Plan"),
            template_path=f"templates/plans/{relative_source}",
            sha256=source.sha256,
            content=source.content,
        )

    def runnable_keys(self) -> tuple[str, ...]:
        return tuple(key for key in self.keys() if self.source(key).compiled.execution is not None)

    def source(self, template_key: str) -> FleetPlanSourceSnapshot:
        key, directory = template_package(
            self.root, template_key, source_name="plan.yml", kind="Plan"
        )
        return self._source(key, directory)

    def _packages(self) -> dict[str, Path]:
        return discover_template_packages(self.root, source_name="plan.yml", kind="Plan")

    def _summary(self, key: str, directory: Path) -> PlanTemplateSummary:
        source = self._source(key, directory)
        relative_source = source.path.relative_to(self.root).as_posix()
        return PlanTemplateSummary(
            template_key=key,
            classification=template_classification(key, kind="Plan"),
            description=template_readme_description(directory, kind="Plan"),
            template_path=f"templates/plans/{relative_source}",
            sha256=source.sha256,
        )

    @staticmethod
    def _source(key: str, directory: Path) -> FleetPlanSourceSnapshot:
        path = validate_template_package(directory, source_name="plan.yml", kind="Plan")
        source = read_fleet_plan_source(
            path,
            kind="plan",
            source=f"template:{key}/plan.yml",
            expected_name=directory.name,
        )
        return source
