from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetExtensionInvalid
from oh_goon.core.models.fleet_plan import DEFAULT_PRE_COMMIT_REQUIREMENT, ManagedExtension
from oh_goon.core.path_authority import navigation_path
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.integrations.fleet_plan_compilation import load_extension_catalog
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


def prepare_declared_plan_runtimes(
    source_graph: FleetPlanSourceGraph,
    *,
    extensions_path: Path | None,
    extensions: FleetExtensionStore,
    tools: FleetToolStore,
) -> tuple[ManagedExtension, ...]:
    """Acquire only runtimes explicitly present in the already-analyzed source graph."""
    required_extensions = source_graph.required_extensions
    imported: tuple[ManagedExtension, ...] = ()
    if required_extensions:
        if extensions_path is None:
            raise FleetExtensionInvalid(
                "Fleet plan references extensions but no --extensions catalog was supplied"
            )
        catalog_path = navigation_path(extensions_path)
        catalog = load_extension_catalog(catalog_path)
        declarations = {item.name: item for item in catalog.extensions}
        if len(declarations) != len(catalog.extensions):
            raise FleetExtensionInvalid("Fleet extension catalog names must be unique")
        missing = [name for name in required_extensions if name not in declarations]
        if missing:
            raise FleetExtensionInvalid(
                "Fleet plan references extensions missing from the catalog: " + ", ".join(missing)
            )
        imported = tuple(
            extensions.sync(declarations[name], catalog_root=catalog_path.parent)
            for name in required_extensions
        )
    elif extensions_path is not None:
        load_extension_catalog(navigation_path(extensions_path))
    if source_graph.uses_pre_commit:
        tools.prepare_pre_commit(DEFAULT_PRE_COMMIT_REQUIREMENT)
    return imported
