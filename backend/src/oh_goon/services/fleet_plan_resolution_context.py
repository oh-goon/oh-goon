from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

from oh_goon.core.models.agent_providers import AgentConnection, ResolvedNativeAgent
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.execution_lock_models import LockedSkillSource
from oh_goon.core.models.fleet_plan import AgentPlanStep, ExtensionCatalog, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputValue
from oh_goon.core.models.fleet_plan_execution_policy import PlanExecutionPolicy
from oh_goon.core.models.git_sources import LockedExecutionSource
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.services.fleet_plan_input_resolution import InputResolutionContext
from oh_goon.services.fleet_plan_resolution_diagnostics import FleetPlanResolutionDiagnostics
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph

type AgentConnectionSelection = AgentConnection | Callable[[], AgentConnection]
type AgentCapture = Callable[
    [Path, AgentPlanStep, str, tuple[PlanInputDefinition, ...], dict[str, PlanInputValue]],
    FrozenAgentStepHandoff,
]


class AgentCaptureOptions(TypedDict):
    inputs: dict[str, PlanInputValue]
    repository_root: Path
    skill_root: Path
    allowed_acceptance: tuple[str, ...]
    extensions_path: Path | None
    cancel_requested: Callable[[], bool] | None
    connection: AgentConnectionSelection | None
    locked_skills: tuple[LockedSkillSource, ...]
    native_agent: NotRequired[ResolvedNativeAgent | None]
    repository: NotRequired[Path | None]
    git_service: NotRequired[GitService | None]
    require_review_phases: NotRequired[bool | None]
    source_graph: NotRequired[FleetPlanSourceGraph | None]
    root_expected_name: NotRequired[str | None]
    locked_sources: NotRequired[tuple[LockedExecutionSource, ...]]


@dataclass(frozen=True)
class PreparedAgentPlan:
    """One captured Agent selection, transported to the original Fleet admission owner."""

    plan: ResolvedFleetPlan
    handoffs: dict[str, FrozenAgentStepHandoff]
    source_sha256: str
    repository: Path
    capture_options: AgentCaptureOptions


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
    agent_handoff: FrozenAgentStepHandoff | None = None
    agent_owner_path: Path | None = None
    agent_capture: AgentCapture | None = None
    agent_connection: AgentConnectionSelection | None = None
    native_agent: ResolvedNativeAgent | None = None
