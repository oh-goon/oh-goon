from __future__ import annotations

import re

from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanInvalid
from oh_goon.core.fleet_plan_output_refs import (
    goon_output_reference,
    replace_step_output_references,
    step_output_reference,
)
from oh_goon.core.models.fleet_plan import (
    CommandPlanStep,
    CompiledExecutionTemplate,
    PlanStep,
    ResolvedPlanStep,
    StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_composition import (
    PlanInputDefinition,
    PlanInputType,
    PlanInputValue,
    PlanStepInclusion,
    validate_domain_input_value,
)
from oh_goon.core.models.fleet_plan_step_contract import StepPlanCacheSource, StepWorkspaceSource

_INPUT_PATTERN = re.compile(r"\$\{\{\s*inputs\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")


def input_reference(value: object) -> str | None:
    """Recognize one complete input reference using the renderer's grammar."""
    match = _INPUT_PATTERN.fullmatch(value) if isinstance(value, str) else None
    return match.group(1) if match is not None else None


def bind_inputs(
    compiled: CompiledExecutionTemplate,
    supplied: dict[str, PlanInputValue],
    *,
    context: str,
) -> dict[str, PlanInputValue]:
    definitions = {item.name: item for item in compiled.inputs}
    ambiguous = sorted(set(supplied).intersection(compiled.ambiguous_inputs))
    if ambiguous:
        raise FleetPlanInputRejected(
            f"Fleet plan {context} inputs are shared by several Goons: {', '.join(ambiguous)}; "
            "route each input explicitly with pass: or with:",
            diagnostic_code="AMBIGUOUS_PROJECTED_INPUT",
            input_names=tuple(ambiguous),
        )
    unknown = sorted(set(supplied) - set(definitions))
    if unknown:
        raise FleetPlanInputRejected(
            f"Fleet plan {context} received unknown inputs: {', '.join(unknown)}",
            diagnostic_code="UNKNOWN_INPUT",
            input_names=tuple(unknown),
        )
    values: dict[str, PlanInputValue] = {}
    missing: list[str] = []
    for name, definition in definitions.items():
        if name in supplied:
            try:
                values[name] = coerce_input(definition, supplied[name], context=context)
            except FleetPlanInvalid as exc:
                raise FleetPlanInputRejected(
                    str(exc), diagnostic_code="INVALID_INPUT_VALUE", input_names=(name,)
                ) from exc
        elif definition.required:
            missing.append(name)
        elif definition.default is None:
            values[name] = None
        else:
            try:
                values[name] = coerce_input(definition, definition.default, context=context)
            except FleetPlanInvalid as exc:
                raise FleetPlanInputRejected(
                    str(exc), diagnostic_code="INVALID_INPUT_DEFAULT", input_names=(name,)
                ) from exc
    if missing:
        raise FleetPlanInputRejected(
            f"Fleet plan {context} requires inputs: {', '.join(sorted(missing))}",
            diagnostic_code="MISSING_REQUIRED_INPUT",
            input_names=tuple(sorted(missing)),
        )
    return values


def coerce_input(
    definition: PlanInputDefinition,
    value: PlanInputValue,
    *,
    context: str,
) -> PlanInputValue:
    prefix = f"Fleet plan {context} input {definition.name!r}"
    if value is None:
        if definition.required:
            raise FleetPlanInvalid(f"{prefix} is required")
        return None
    if definition.input_type in {
        PlanInputType.STRING,
        PlanInputType.HOSTNAME,
        PlanInputType.REPOSITORY_PATH,
        PlanInputType.GIT_REVISION,
        PlanInputType.DIGEST,
        PlanInputType.SEMVER,
        PlanInputType.DURATION,
        PlanInputType.KUBERNETES_NAME,
        PlanInputType.KUBERNETES_DEPLOYMENT_NAME,
        PlanInputType.OCI_REPOSITORY,
        PlanInputType.OCI_REFERENCE,
        PlanInputType.WORKSPACE_REFERENCE,
        PlanInputType.REPOSITORY_REFERENCE,
        PlanInputType.SKILL_REFERENCE,
        PlanInputType.ACCEPTANCE_PROFILE,
    }:
        if not isinstance(value, str):
            raise FleetPlanInvalid(f"{prefix} must be a string")
        if definition.non_empty and not value.strip():
            raise FleetPlanInvalid(f"{prefix} must be non-empty")
        if definition.input_type is PlanInputType.STRING:
            if definition.pattern is not None and re.fullmatch(definition.pattern, value) is None:
                raise FleetPlanInvalid(f"{prefix} must match {definition.pattern!r}")
            if definition.starts_with is not None and not value.startswith(definition.starts_with):
                raise FleetPlanInvalid(f"{prefix} must start with {definition.starts_with!r}")
            return value
        try:
            validate_domain_input_value(definition, value, label="value")
        except ValueError as exc:
            raise FleetPlanInvalid(
                f"{prefix} has invalid {definition.input_type.value} syntax"
            ) from exc
        return value
    if definition.input_type is PlanInputType.BOOLEAN:
        if type(value) is bool:
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise FleetPlanInvalid(f"{prefix} must be true or false")
    if definition.input_type is PlanInputType.INTEGER:
        result = _integer_input(value, prefix=prefix)
        if definition.minimum is not None and result < definition.minimum:
            raise FleetPlanInvalid(f"{prefix} must be >= {definition.minimum}")
        if definition.maximum is not None and result > definition.maximum:
            raise FleetPlanInvalid(f"{prefix} must be <= {definition.maximum}")
        return result
    if definition.input_type is PlanInputType.CHOICE:
        if not isinstance(value, str) or value not in definition.choices:
            raise FleetPlanInvalid(f"{prefix} must be one of: {', '.join(definition.choices)}")
        return value
    raise AssertionError(f"Unhandled Fleet input type: {definition.input_type}")


def _integer_input(value: PlanInputValue, *, prefix: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    raise FleetPlanInvalid(f"{prefix} must be an integer")


def evaluate_inclusion(
    compiled: CompiledExecutionTemplate,
    inclusion: PlanStepInclusion,
    values: dict[str, PlanInputValue],
) -> tuple[bool, str]:
    definition = next(item for item in compiled.inputs if item.name == inclusion.input)
    actual = values[inclusion.input]
    if inclusion.equals is not None:
        expected = coerce_input(definition, inclusion.equals, context=compiled.name)
        return actual == expected, _inclusion_reason(inclusion.input, "==", expected, actual)
    assert inclusion.not_equals is not None
    expected = coerce_input(definition, inclusion.not_equals, context=compiled.name)
    return actual != expected, _inclusion_reason(inclusion.input, "!=", expected, actual)


def _inclusion_reason(
    name: str,
    operator: str,
    expected: PlanInputValue,
    actual: PlanInputValue,
) -> str:
    return f"include_when {name} {operator} {_input_text(expected)}; actual={_input_text(actual)}"


def render_step(step: PlanStep, values: dict[str, PlanInputValue]) -> PlanStep:
    payload = _render_value(step.model_dump(mode="python"), values)
    if isinstance(step, CommandPlanStep):
        payload["argv"] = tuple(str(value) for value in payload["argv"])
    return type(step).model_validate(payload)


def _render_value(value, inputs: dict[str, PlanInputValue]):
    if isinstance(value, str):
        direct = input_reference(value)
        if direct is not None:
            name = direct
            if name not in inputs:
                raise FleetPlanInvalid(f"Fleet plan references unresolved input {name!r}")
            return inputs[name]
        return _INPUT_PATTERN.sub(lambda match: _render_input(match, inputs), value)
    if isinstance(value, tuple):
        return tuple(_render_value(item, inputs) for item in value)
    if isinstance(value, list):
        return [_render_value(item, inputs) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, inputs) for key, item in value.items()}
    return value


def _render_input(match: re.Match[str], inputs: dict[str, PlanInputValue]) -> str:
    name = match.group(1)
    if name not in inputs:
        raise FleetPlanInvalid(f"Fleet plan references unresolved input {name!r}")
    if inputs[name] is None:
        raise FleetPlanInvalid(
            f"Fleet nullable input {name!r} cannot be rendered directly; bind it or provide a default before use"
        )
    return _input_text(inputs[name])


def _input_text(value: PlanInputValue) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


def qualify_step_dependencies(
    step: PlanStep,
    prefix: str,
    goon_aliases: dict[str, dict[str, str]],
    *,
    preserved_step_ids: frozenset[str] = frozenset(),
) -> PlanStep:
    payload = resolve_output_references(
        step.model_dump(mode="python"),
        prefix=prefix,
        goon_aliases=goon_aliases,
        preserved_step_ids=preserved_step_ids,
    )
    qualified_step = type(step).model_validate(payload)
    workspace_source = getattr(getattr(qualified_step, "workspace", None), "source", None)
    if isinstance(workspace_source, StepWorkspaceSource):
        if workspace_source.step_id in goon_aliases:
            raise FleetPlanInvalid(
                f"Fleet workspace cannot target template step {workspace_source.step_id!r}; "
                "target one concrete published workspace instead"
            )
        if workspace_source.step_id not in preserved_step_ids:
            qualified_source = workspace_source.model_copy(
                update={"step_id": _qualified_step_id(prefix, workspace_source.step_id)}
            )
            qualified_step = qualified_step.model_copy(
                update={
                    "workspace": qualified_step.workspace.model_copy(
                        update={"source": qualified_source}
                    )
                }
            )
    cache_source = getattr(getattr(qualified_step, "cache", None), "source", None)
    if isinstance(cache_source, StepPlanCacheSource):
        if cache_source.step_id in goon_aliases:
            raise FleetPlanInvalid(
                f"Fleet cache cannot target template step {cache_source.step_id!r}; "
                "target one concrete published cache instead"
            )
        qualified_cache_source = cache_source.model_copy(
            update={"step_id": _qualified_step_id(prefix, cache_source.step_id)}
        )
        qualified_step = qualified_step.model_copy(
            update={
                "cache": qualified_step.cache.model_copy(update={"source": qualified_cache_source})
            }
        )
    condition = getattr(qualified_step, "when", None)
    if not isinstance(condition, StepStatusCondition):
        return qualified_step
    if condition.step_id in goon_aliases:
        raise FleetPlanInvalid(
            f"Fleet step_status cannot target template step {condition.step_id!r}; "
            "target one concrete step instead"
        )
    qualified = condition.model_copy(
        update={"step_id": _qualified_step_id(prefix, condition.step_id)}
    )
    return qualified_step.model_copy(update={"when": qualified})


def resolve_output_references(
    value,
    *,
    prefix: str,
    goon_aliases: dict[str, dict[str, str]],
    preserved_step_ids: frozenset[str] = frozenset(),
):
    if isinstance(value, str):
        return _resolve_output_reference(
            value,
            prefix=prefix,
            goon_aliases=goon_aliases,
            preserved_step_ids=preserved_step_ids,
        )
    if isinstance(value, tuple):
        return tuple(
            resolve_output_references(
                item,
                prefix=prefix,
                goon_aliases=goon_aliases,
                preserved_step_ids=preserved_step_ids,
            )
            for item in value
        )
    if isinstance(value, list):
        return [
            resolve_output_references(
                item,
                prefix=prefix,
                goon_aliases=goon_aliases,
                preserved_step_ids=preserved_step_ids,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: resolve_output_references(
                item,
                prefix=prefix,
                goon_aliases=goon_aliases,
                preserved_step_ids=preserved_step_ids,
            )
            for key, item in value.items()
        }
    return value


def _resolve_output_reference(
    value: str,
    *,
    prefix: str,
    goon_aliases: dict[str, dict[str, str]],
    preserved_step_ids: frozenset[str],
) -> str:
    goon_reference = goon_output_reference(value)
    if goon_reference is not None:
        goon_id, output = goon_reference
        concrete = goon_aliases.get(goon_id, {}).get(output)
        if concrete is None:
            raise FleetPlanInvalid(f"Goon import {goon_id} does not publish output {output!r}")
        return concrete

    def replace(step_id: str, output: str) -> str | None:
        if step_id in preserved_step_ids:
            return None
        if step_id in goon_aliases:
            concrete = goon_aliases[step_id].get(output)
            if concrete is None:
                raise FleetPlanInvalid(f"Goon import {step_id} does not publish output {output!r}")
            return concrete
        qualified = _qualified_step_id(prefix, step_id)
        return f"${{{{ steps.{qualified}.outputs.{output} }}}}"

    return replace_step_output_references(value, replace)


def resolve_goon_outputs(
    compiled: CompiledExecutionTemplate,
    *,
    prefix: str,
    goon_aliases: dict[str, dict[str, str]],
    steps: tuple[ResolvedPlanStep, ...],
) -> dict[str, str]:
    published = {
        step.step_id: {item.name for item in getattr(step, "outputs", ())} for step in steps
    }
    result: dict[str, str] = {}
    for output in compiled.outputs:
        concrete = resolve_output_references(
            output.source, prefix=prefix, goon_aliases=goon_aliases
        )
        reference = step_output_reference(concrete)
        if reference is None:
            raise FleetPlanInvalid(
                f"Goon output {output.name!r} must resolve to one concrete step output"
            )
        step_id, output_name = reference
        if step_id not in published:
            raise FleetPlanInvalid(
                f"Goon output {output.name!r} references missing step {step_id!r}"
            )
        if output_name not in published[step_id]:
            raise FleetPlanInvalid(
                f"Goon output {output.name!r} references undeclared output {step_id}.{output_name}"
            )
        result[output.name] = concrete
    return result


def validate_condition_dependencies(steps: tuple[ResolvedPlanStep, ...]) -> None:
    prior: set[str] = set()
    for step in steps:
        condition = getattr(step, "when", None)
        if isinstance(condition, StepStatusCondition) and condition.step_id not in prior:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} condition must reference an earlier resolved step: "
                f"{condition.step_id}"
            )
        prior.add(step.step_id)


def _qualified_step_id(prefix: str, step_id: str) -> str:
    return f"{prefix}.{step_id}" if prefix else step_id
