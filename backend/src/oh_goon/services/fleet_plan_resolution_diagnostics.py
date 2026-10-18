from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.fleet_plan_composition import evaluate_inclusion
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate, PlanStep, ResolvedPlanStep
from oh_goon.core.models.fleet_plan_composition import (
    PlanInputDefinition,
    PlanInputValue,
    PlanStepInclusion,
)
from oh_goon.core.models.fleet_plan_diagnostics import (
    PlanResolutionDisposition,
    PlanResolutionEntry,
    PlanValueNamespace,
    PlanValueResolutionEntry,
    relative_diagnostic_path,
)
from oh_goon.integrations.fleet_plan_compilation import qualified_step_id


@dataclass(frozen=True)
class FleetPlanResolutionDiagnostics:
    """Own optional compile-time provenance without changing executable plan semantics."""

    plan_root: Path
    entries: list[PlanResolutionEntry] | None
    values: list[PlanValueResolutionEntry] | None

    def trace_pipeline_values(
        self,
        *,
        plan_path: Path,
        compiled: CompiledExecutionTemplate,
        prefix: str,
        input_values: dict[str, PlanInputValue],
        input_origins: dict[str, str],
        compiler: PipelineValueCompiler,
    ) -> None:
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
        for definition in compiled.env:
            resolved_value = compiler.describe_env(definition.name)
            self.values.append(
                PlanValueResolutionEntry(
                    source=source,
                    scope=scope,
                    namespace=PlanValueNamespace.ENV,
                    name=definition.name,
                    origin=definition.source.kind,
                    declared=definition.source.model_dump_json(),
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
