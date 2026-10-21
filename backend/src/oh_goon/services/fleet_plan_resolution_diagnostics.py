from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from oh_goon.core.fleet_plan_composition import evaluate_inclusion
from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate, PlanStep, ResolvedPlanStep
from oh_goon.core.models.fleet_plan_composition import (
    PlanInputDefinition,
    PlanInputValue,
    PlanStepInclusion,
)
from oh_goon.core.models.fleet_plan_diagnostics import (
    PlanConfigurationBinding,
    PlanConfigurationEntry,
    PlanConfigurationEvidence,
    PlanResolutionDisposition,
    PlanResolutionEntry,
    PlanValueNamespace,
    PlanValueResolutionEntry,
    relative_diagnostic_path,
)
from oh_goon.integrations.fleet_plan_compilation import qualified_step_id
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


@dataclass
class FleetPlanResolutionDiagnostics:
    """Capture bounded safe lineage; optional legacy explain values stay transient."""

    plan_root: Path
    entries: list[PlanResolutionEntry] | None
    values: list[PlanValueResolutionEntry] | None
    source_graph: FleetPlanSourceGraph | None = None
    configuration: list[PlanConfigurationEntry] = field(default_factory=list)
    omitted_entries: int = 0

    def evidence(self) -> PlanConfigurationEvidence:
        return PlanConfigurationEvidence(
            root_source_sha256=(
                self.source_graph.root.source_sha256 if self.source_graph else None
            ),
            entries=tuple(self.configuration),
            omitted_entries=self.omitted_entries,
        )

    def _record(self, entry: PlanConfigurationEntry) -> None:
        if len(self.configuration) < 4096:
            self.configuration.append(entry)
        else:
            self.omitted_entries += 1

    def trace_pipeline_values(
        self,
        *,
        plan_path: Path,
        compiled: CompiledExecutionTemplate,
        prefix: str,
        input_values: dict[str, PlanInputValue],
        input_origins: dict[str, str],
        compiler: PipelineValueCompiler,
        input_bindings: Mapping[str, PlanConfigurationBinding] | None = None,
    ) -> None:
        node = self.source_graph.node(plan_path) if self.source_graph else None
        for definition in compiled.inputs:
            name = definition.name
            value = input_values[name]
            binding = (input_bindings or {}).get(name) or PlanConfigurationBinding(
                kind="caller" if input_origins.get(name) == "supplied" else "default"
            )
            position = node.value_positions.get(f"inputs/{name}") if node else None
            default_declared = (
                f"inputs/{name}/default" in node.value_positions
                if node is not None and position is not None
                else None
            )
            if binding.kind == "default" and default_declared is False and value is None:
                binding = binding.model_copy(update={"kind": "optional_omission"})
            self._record(
                PlanConfigurationEntry(
                    scope=prefix or None,
                    namespace=PlanValueNamespace.INPUT,
                    name=name,
                    input_type=definition.input_type,
                    required=definition.required,
                    default_declared=default_declared,
                    value_state=(
                        "deferred"
                        if binding.kind
                        in {"producer_workspace", "deferred_goon_output", "deferred_step_output"}
                        or (isinstance(value, str) and step_output_reference(value) is not None)
                        else "null"
                        if value is None
                        else "empty"
                        if value == ""
                        else "defined"
                    ),
                    binding=binding,
                    source_sha256=node.source_sha256 if node else None,
                    line=position.line if position else None,
                    column=position.column if position else None,
                )
            )
        for env_definition in compiled.env:
            position = node.value_positions.get(f"env/{env_definition.name}") if node else None
            self._record(
                PlanConfigurationEntry(
                    scope=prefix or None,
                    namespace=PlanValueNamespace.ENV,
                    name=env_definition.name,
                    value_state=(
                        "deferred"
                        if env_definition.source.kind == "host"
                        else "empty"
                        if env_definition.source.value == ""
                        else "defined"
                    ),
                    binding=PlanConfigurationBinding(kind=env_definition.source.kind),
                    source_sha256=node.source_sha256 if node else None,
                    line=position.line if position else None,
                    column=position.column if position else None,
                )
            )
        if self.values is None:
            return
        source = relative_diagnostic_path(plan_path, self.plan_root)
        scope = prefix or "root"
        definitions = {item.name: item for item in compiled.inputs}
        for name, value in input_values.items():
            definition = definitions[name]
            origin = input_origins.get(name, "default")
            contains_host_value = origin.startswith("env:")
            self.values.append(
                PlanValueResolutionEntry(
                    source=source,
                    scope=scope,
                    namespace=PlanValueNamespace.INPUT,
                    name=name,
                    origin=origin,
                    declared=_declared_input(definition),
                    resolved=(
                        _redacted_external_input(origin)
                        if contains_host_value
                        else _trace_scalar(value)
                    ),
                    contains_host_value=contains_host_value,
                )
            )
        for env_definition in compiled.env:
            resolved_value = compiler.describe_env(env_definition.name)
            self.values.append(
                PlanValueResolutionEntry(
                    source=source,
                    scope=scope,
                    namespace=PlanValueNamespace.ENV,
                    name=env_definition.name,
                    origin=env_definition.source.kind,
                    declared=env_definition.source.model_dump_json(),
                    resolved=resolved_value.trace_template(),
                    contains_host_value=resolved_value.has_host_source,
                )
            )

    def should_omit_step(
        self,
        *,
        plan_path: Path,
        compiled: CompiledExecutionTemplate,
        step: PlanStep,
        inclusion: PlanStepInclusion,
        prefix: str,
        input_values: dict[str, PlanInputValue],
    ) -> bool:
        included, reason = evaluate_inclusion(compiled, inclusion, input_values)
        if included:
            return False
        if self.entries is not None:
            self.entries.append(
                PlanResolutionEntry(
                    source=relative_diagnostic_path(plan_path, self.plan_root),
                    step_id=qualified_step_id(prefix, step.step_id),
                    kind=step.kind,
                    disposition=PlanResolutionDisposition.OMITTED,
                    reason=reason,
                )
            )
        return True

    def trace_expansion(
        self,
        *,
        plan_path: Path,
        step_id: str,
        prefix: str,
        compiled_step_ids: tuple[str, ...],
    ) -> None:
        if self.entries is None:
            return
        self.entries.append(
            PlanResolutionEntry(
                source=relative_diagnostic_path(plan_path, self.plan_root),
                step_id=qualified_step_id(prefix, step_id),
                kind="goon",
                disposition=PlanResolutionDisposition.EXPANDED,
                compiled_step_ids=compiled_step_ids,
                reason="expanded Composite Goon",
            )
        )

    def trace_included(self, *, plan_path: Path, step: ResolvedPlanStep) -> None:
        if self.entries is None:
            return
        self.entries.append(
            PlanResolutionEntry(
                source=relative_diagnostic_path(plan_path, self.plan_root),
                step_id=step.step_id,
                kind=step.kind,
                disposition=PlanResolutionDisposition.INCLUDED,
                compiled_step_ids=(step.step_id,),
            )
        )


def _declared_input(definition: PlanInputDefinition) -> str:
    parts = [f"type={definition.input_type.value}"]
    if definition.required:
        parts.append("required=true")
    if definition.default is not None:
        parts.append(f"default={_trace_scalar(definition.default)}")
    if definition.choices:
        parts.append(f"choices={','.join(definition.choices)}")
    if definition.minimum is not None:
        parts.append(f"min={definition.minimum}")
    if definition.maximum is not None:
        parts.append(f"max={definition.maximum}")
    if definition.non_empty:
        parts.append("non_empty=true")
    if definition.pattern is not None:
        parts.append(f"pattern={definition.pattern!r}")
    return " ".join(parts)


def _trace_scalar(value: PlanInputValue) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


def _redacted_external_input(origin: str) -> str:
    _, _, name = origin.partition(":")
    label = name or "value"
    return f"<env:{label}:redacted>"
