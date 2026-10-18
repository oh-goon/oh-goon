from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from oh_goon.core.capability_model import CapabilityDefinition, CapabilityRunnerKind
from oh_goon.core.capability_registry import capability_by_id
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.vocabulary import CommandSafety, OperationAction
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    publish_prepared_directory,
    replace_prepared_file,
)

_DEFINITION_OWNERS = MappingProxyType(
    {
        CapabilityRunnerKind.ENGINE: "backend/src/oh_goon/core/capability_engine_definitions.py",
        CapabilityRunnerKind.RECIPE: "backend/src/oh_goon/core/capability_recipe_definitions.py",
        CapabilityRunnerKind.MIGRATION: "backend/src/oh_goon/core/capability_migration_definitions.py",
        CapabilityRunnerKind.FLEET_PLAN: "backend/src/oh_goon/core/capability_plan_definitions.py",
    }
)
_IMPLEMENTATION_OWNERS = MappingProxyType(
    {
        CapabilityRunnerKind.ENGINE: "backend/src/oh_goon/engine/runner.py",
        CapabilityRunnerKind.RECIPE: "backend/src/oh_goon/integrations/recipes.py",
        CapabilityRunnerKind.MIGRATION: "backend/src/oh_goon/integrations/migration.py",
        CapabilityRunnerKind.FLEET_PLAN: "backend/src/oh_goon/integrations/fleet_plans.py",
    }
)
_RUNTIME_ROOT_OWNER = "backend/src/oh_goon/application.py"
_RUNNER_WIRING_OWNER = "backend/src/oh_goon/_application_foundation.py"
_SELECTOR_OWNER = "backend/src/oh_goon/services/target_selection.py"
_API_OWNER = "backend/src/oh_goon/api/routes/capabilities.py"


@dataclass(frozen=True, slots=True)
class CapabilityScaffoldRequest:
    capability_id: str
    action: OperationAction
    runner_kind: CapabilityRunnerKind
    label: str
    description: str
    target_kind: CapabilityTargetKind = CapabilityTargetKind.NONE
    safety: CommandSafety = CommandSafety.READ_ONLY


@dataclass(frozen=True, slots=True)
class CapabilityScaffoldPacket:
    definition_owner: str
    implementation_owner: str
    definition_snippet: str
    test_snippet: str
    checklist: tuple[str, ...]

    def render(self) -> str:
        checklist = "\n".join(f"- {item}" for item in self.checklist)
        return (
            "CAPABILITY GOLDEN PATH\n\n"
            f"Definition owner:     {self.definition_owner}\n"
            f"Implementation owner: {self.implementation_owner}\n"
            f"Runtime root:         {_RUNTIME_ROOT_OWNER}\n"
            f"Runner wiring:        {_RUNNER_WIRING_OWNER}\n"
            f"Target selection:     {_SELECTOR_OWNER}\n"
            f"API exposure:         {_API_OWNER}\n\n"
            "DEFINITION SNIPPET\n"
            "------------------\n"
            f"{self.definition_snippet}\n\n"
            "TEST SKELETON\n"
            "-------------\n"
            f"{self.test_snippet}\n\n"
            "CHECKLIST\n"
            "---------\n"
            f"{checklist}\n"
        )

    def write(self, output: Path) -> tuple[Path, Path, Path]:
        output.parent.mkdir(parents=True, exist_ok=True)
        with exact_file_write_lock(output):
            if output.exists():
                raise StateConflict(f"Capability scaffold output already exists: {output}")
            staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
            try:
                _write_staged_file(
                    staging / "capability_definition.py", self.definition_snippet + "\n"
                )
                _write_staged_file(staging / "test_capability.py", self.test_snippet + "\n")
                _write_staged_file(staging / "IMPLEMENTATION.md", self.render())
                if output.exists():
                    raise StateConflict(f"Capability scaffold output already exists: {output}")
                publish_prepared_directory(staging, output)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return (
            output / "capability_definition.py",
            output / "test_capability.py",
            output / "IMPLEMENTATION.md",
        )


def _write_staged_file(target: Path, content: str) -> None:
    with prepared_text_file(target, content) as temporary:
        replace_prepared_file(temporary, target)


def describe_capability(capability_id: str) -> str:
    capability = capability_by_id(capability_id)
    return _render_capability(capability)


def scaffold_capability(request: CapabilityScaffoldRequest) -> CapabilityScaffoldPacket:
    definition_owner = _DEFINITION_OWNERS[request.runner_kind]
    implementation_owner = _IMPLEMENTATION_OWNERS[request.runner_kind]
    definition = _definition_snippet(request)
    test = _test_snippet(request)
    checklist = (
        f"Paste/adapt the definition in {definition_owner}; keep executable runner code out of core.",
        f"Implement the runner contract in {implementation_owner}; do not add a pass-through adapter layer.",
        f"Add a new selector in {_SELECTOR_OWNER} only if the existing selector vocabulary cannot express target admission.",
        "Do not add capability-specific branches to FleetService, FleetRunControlPlane, or frontend concurrency controllers.",
        "Run `uv run --locked --project backend oh-goon dev capability show <id>` after registration to verify the resolved ownership path.",
        "Run `make architecture`, `make generate`, and the focused tests before finishing.",
    )
    return CapabilityScaffoldPacket(
        definition_owner=definition_owner,
        implementation_owner=implementation_owner,
        definition_snippet=definition,
        test_snippet=test,
        checklist=checklist,
    )


def _render_capability(capability: CapabilityDefinition) -> str:
    return "\n".join(
        (
            f"Capability:          {capability.capability_id}",
            f"Action:              {capability.action.value}",
            f"Runner family:       {capability.runner_kind.value}",
            f"Target kind:         {capability.target_kind.value}",
            f"Safety:              {capability.safety.value}",
            f"Definition owner:    {_DEFINITION_OWNERS[capability.runner_kind]}",
            f"Implementation:      {_IMPLEMENTATION_OWNERS[capability.runner_kind]}",
            f"Runtime root:        {_RUNTIME_ROOT_OWNER}",
            f"Runner wiring:       {_RUNNER_WIRING_OWNER}",
            f"Target selection:    {_SELECTOR_OWNER}",
            f"API projection:      {_API_OWNER}",
            "Safety boundary:     ExecutionSession owner transaction -> Journal + RepositoryEffectAttemptStore",
            "Read authority:      JournalReadView -> pure projections",
        )
    )


def _definition_snippet(request: CapabilityScaffoldRequest) -> str:
    return "\n".join(
        (
            "CapabilityDefinition(",
            f'    capability_id="{request.capability_id}",',
            f"    action=OperationAction.{request.action.name},",
            f'    label="{request.label}",',
            f'    description="{request.description}",',
            f"    runner_kind=CapabilityRunnerKind.{request.runner_kind.name},",
            f"    target_kind=CapabilityTargetKind.{request.target_kind.name},",
            f"    safety=CommandSafety.{request.safety.name},",
            "    # Add selector/prerequisites/invalidation only when the domain rule requires them.",
            ")",
        )
    )


def _test_snippet(request: CapabilityScaffoldRequest) -> str:
    return "\n".join(
        (
            "from oh_goon.core.capability_registry import capability_by_id",
            "",
            "",
            f"def test_{request.capability_id.replace('-', '_').replace(':', '_')}_is_registered() -> None:",
            f'    capability = capability_by_id("{request.capability_id}")',
            f'    assert capability.capability_id == "{request.capability_id}"',
            f'    assert capability.action.value == "{request.action.value}"',
        )
    )
