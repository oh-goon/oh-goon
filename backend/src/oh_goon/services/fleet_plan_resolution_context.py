from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.models.fleet_plan import ExtensionCatalog
from oh_goon.core.models.fleet_plan_execution_policy import PlanExecutionPolicy
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.services.fleet_plan_resolution_diagnostics import FleetPlanResolutionDiagnostics
from oh_goon.services.fleet_plan_input_resolution import InputResolutionContext
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


@dataclass(frozen=True)
class FleetPlanResolutionContext:
    plan_root: Path
    execution: PlanExecutionPolicy
    catalog: ExtensionCatalog | None
    extension_root: Path | None
    extensions: FleetExtensionStore | None
    tools: FleetToolStore | None
    diagnostics: FleetPlanResolutionDiagnostics
    source_graph: FleetPlanSourceGraph
    input_resolution: InputResolutionContext
    agent_handoff: object | None = None
    agent_owner_path: Path | None = None
