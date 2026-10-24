from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import input_reference
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate, CompiledGoonImportStep
from oh_goon.core.models.fleet_plan_composition import (
    EnvInputSource,
    GitInputSource,
    PlanInputBinding,
    PlanInputDefinition,
    PlanInputSource,
    PlanInputValue,
    TextInputSource,
    TomlInputSource,
    YamlInputSource,
)
from oh_goon.core.models.fleet_plan_diagnostics import PlanInputSourceObservation
from oh_goon.core.path_authority import RepositoryPathSpace, navigation_path
from oh_goon.engine.git import GitService
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


@dataclass(frozen=True, slots=True)
class InputResolutionContext:
    """External value authority used only while resolving canonical Goon inputs."""

    repository: Path | None = None
    environment: Mapping[str, str] | None = None
    git: GitService = GitService()
    observe_environment: bool = True


def resolve_input_sources(
    sources: tuple[PlanInputSource, ...],
    *,
    context: InputResolutionContext,
    input_name: str,
    observations: list[PlanInputSourceObservation] | None = None,
) -> tuple[bool, PlanInputValue, str]:
    """Return the first defined source value; no expressions or computation are supported."""

    for index, source in enumerate(sources):
        found, value, origin, state = _resolve_source(
            source, context=context, input_name=input_name
        )
        if observations is not None:
            observations.append(
                PlanInputSourceObservation(
                    kind=source.kind,
                    state=state,
                    name=source.name if isinstance(source, EnvInputSource) else None,
                    git_field=source.field if isinstance(source, GitInputSource) else None,
                )
            )
        if found:
            if observations is not None:
                observations.extend(
                    PlanInputSourceObservation(kind=later.kind, state="not_evaluated")
                    for later in sources[index + 1 :]
                )
            return True, value, origin
    return False, None, "default"


def _resolve_source(
    source: PlanInputSource,
    *,
    context: InputResolutionContext,
    input_name: str,
) -> tuple[bool, PlanInputValue, str, Literal["missing", "null", "empty", "selected"]]:
    if isinstance(source, EnvInputSource):
        if not context.observe_environment:
            raise FleetPlanInputRejected(
                "Environment-backed input was not observed by read-only inspection",
                diagnostic_code="EXTERNAL_INPUT_NOT_OBSERVED",
                input_names=(input_name,),
            )
        environment = context.environment
        if environment is None:
            return False, None, f"env:{source.name}", "missing"
        environment_value = environment.get(source.name)
        if environment_value is None or environment_value == "":
            return (
                False,
                None,
                f"env:{source.name}",
                "missing" if environment_value is None else "empty",
            )
        return True, environment_value, f"env:{source.name}", "selected"

    if isinstance(source, YamlInputSource):
        repository = _require_repository(context, input_name=input_name, source="yaml")
        path = RepositoryPathSpace.from_root(repository).resolve(
            source.path,
            label=f"Fleet input {input_name!r} YAML source",
            kind="file",
            require_exists=False,
        )
        if not path.exists():
            return False, None, f"yaml:{source.path}#{source.key}", "missing"
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} cannot read YAML source {source.path}: {exc}"
            ) from exc
        value = _mapping_key(payload, source.key)
        if value is _MISSING or value is None or value == "":
            return False, None, f"yaml:{source.path}#{source.key}", _undefined_state(value)
        if type(value) not in (str, bool, int):
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} YAML source {source.path}#{source.key} "
                "must resolve to a scalar"
            )
        return True, cast(PlanInputValue, value), f"yaml:{source.path}#{source.key}", "selected"

    if isinstance(source, TomlInputSource):
        repository = _require_repository(context, input_name=input_name, source="toml")
        path = RepositoryPathSpace.from_root(repository).resolve(
            source.path,
            label=f"Fleet input {input_name!r} TOML source",
            kind="file",
            require_exists=False,
        )
        if not path.exists():
            return False, None, f"toml:{source.path}#{source.key}", "missing"
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} cannot read TOML source {source.path}: {exc}"
            ) from exc
        value = _mapping_key(payload, source.key)
        if value is _MISSING or value is None or value == "":
            return False, None, f"toml:{source.path}#{source.key}", _undefined_state(value)
        if type(value) not in (str, bool, int):
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} TOML source {source.path}#{source.key} "
                "must resolve to a scalar"
            )
        return True, cast(PlanInputValue, value), f"toml:{source.path}#{source.key}", "selected"

    if isinstance(source, TextInputSource):
        repository = _require_repository(context, input_name=input_name, source="text")
        path = RepositoryPathSpace.from_root(repository).resolve(
            source.path,
            label=f"Fleet input {input_name!r} text source",
            kind="file",
            require_exists=False,
        )
        if not path.exists():
            return False, None, f"text:{source.path}", "missing"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} cannot read text source {source.path}: {exc}"
            ) from exc
        if not value:
            return False, None, f"text:{source.path}", "empty"
        if "\n" in value or "\r" in value:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} text source {source.path} "
                "must contain exactly one non-empty line"
            )
        return True, value, f"text:{source.path}", "selected"

    if isinstance(source, GitInputSource):
        repository = _require_repository(context, input_name=input_name, source="git")
        observation = context.git.observe(repository)
        if not observation.is_repository:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} git source requires a Git repository"
            )
        if source.field in {"commit_sha", "commit_short_sha"}:
            head = observation.head_sha
            if not head:
                return False, None, f"git:{source.field}", "missing"
            value = head if source.field == "commit_sha" else head[:8]
            return True, value, f"git:{source.field}", "selected"
        if source.field == "branch":
            if observation.branch is None:
                return False, None, "git:branch", "missing"
            return True, observation.branch, "git:branch", "selected"
        raise AssertionError(f"Unhandled Git input source field: {source.field}")

    raise AssertionError(f"Unhandled Fleet input source: {type(source).__name__}")


def _require_repository(
    context: InputResolutionContext,
    *,
    input_name: str,
    source: str,
) -> Path:
    if context.repository is None:
        raise FleetPlanInvalid(
            f"Fleet input {input_name!r} {source} source requires repository-bound resolution"
        )
    return context.repository.resolve()


_MISSING = object()


def _undefined_state(value: object) -> Literal["missing", "null", "empty"]:
    if value is _MISSING:
        return "missing"
    return "null" if value is None else "empty"


def _mapping_key(payload: object, key: str) -> object:
    current = payload
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def materialize_projected_inputs(
    source_graph: FleetPlanSourceGraph,
    path: Path,
    *,
    _cache: dict[Path, CompiledExecutionTemplate] | None = None,
) -> CompiledExecutionTemplate:
    """Derive public input contracts from their declaring Goons.

    Plans expose unambiguous, unbound direct inputs automatically. Composite
    Goons retain their explicitly declared interface. Explicit references select
    recipients, including intentional fan-out; an unbound shared name never does.
    """

    cache = {} if _cache is None else _cache
    canonical = navigation_path(path)
    cached = cache.get(canonical)
    if cached is not None:
        return cached
    compiled = source_graph.node(canonical).compiled
    automatic = compiled.authoring_kind == "plan" and not compiled.projected_inputs
    if not compiled.projected_inputs and not automatic:
        cache[canonical] = compiled
        return compiled
    if compiled.inputs:
        raise FleetPlanInvalid(
            "Execution template cannot mix projected and explicitly defined inputs"
        )

    steps = list(compiled.steps)
    children = {}
    names = dict.fromkeys(compiled.projected_inputs)
    for index, step in enumerate(steps):
        if not isinstance(step, CompiledGoonImportStep):
            continue
        child_node = source_graph.goon(canonical, step)
        child = materialize_projected_inputs(source_graph, child_node.path, _cache=cache)
        contracts = {item.name: item for item in child.inputs}
        children[index] = contracts
        if automatic:
            bound = {binding.name for binding in step.inputs}
            for binding in step.inputs:
                reference = input_reference(binding.value)
                if reference is not None and not binding.sources:
                    names[reference] = None
            for name in contracts:
                if name not in bound:
                    names[name] = None
    definitions: list[PlanInputDefinition] = []
    ambiguous: list[str] = []
    for name in names:
        expected_reference = f"${{{{ inputs.{name} }}}}"
        recipients: list[tuple[str, PlanInputDefinition]] = []
        candidates: list[tuple[int, CompiledGoonImportStep, PlanInputDefinition]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, CompiledGoonImportStep):
                continue
            contracts = children[index]
            for binding in step.inputs:
                if binding.sources or input_reference(binding.value) != name:
                    continue
                if binding.name not in contracts:
                    raise FleetPlanInvalid(
                        f"Projected input {name!r} binds unknown child input "
                        f"{binding.name!r} on {step.step_id}"
                    )
                recipients.append((step.step_id, contracts[binding.name]))
            if name in contracts and not any(binding.name == name for binding in step.inputs):
                candidates.append((index, step, contracts[name]))
        if not recipients:
            if not candidates:
                raise FleetPlanInputRejected(
                    f"Projected input {name!r} is not owned by any directly selected Goon; "
                    "use with: to bind a renamed input",
                    diagnostic_code="UNOWNED_PROJECTED_INPUT",
                    input_names=(name,),
                )
            if len(candidates) != 1:
                if automatic:
                    ambiguous.append(name)
                    continue
                raise FleetPlanInputRejected(
                    f"Plan projected input {name!r} is ambiguous across direct Goons; "
                    "select recipients explicitly with pass: or with:",
                    diagnostic_code="AMBIGUOUS_PROJECTED_INPUT",
                    input_names=(name,),
                )
            index, step, definition = candidates[0]
            steps[index] = step.model_copy(
                update={
                    "inputs": (
                        *step.inputs,
                        PlanInputBinding(name=name, value=expected_reference),
                    )
                }
            )
            recipients.append((step.step_id, definition))
        first_owner, first = recipients[0]
        first_contract = first.model_dump(mode="python", exclude={"name", "description"})
        incompatible = [
            owner
            for owner, definition in recipients[1:]
            if definition.model_dump(mode="python", exclude={"name", "description"})
            != first_contract
        ]
        if incompatible:
            raise FleetPlanInvalid(
                f"Projected input {name!r} has incompatible child contracts: "
                + ", ".join([first_owner, *incompatible])
            )
        definitions.append(first.model_copy(update={"name": name}))

    if len(definitions) > 32:
        raise FleetPlanInvalid(
            "Plan exposes more than 32 inputs; select its public interface with inputs:"
        )
    materialized = compiled.model_copy(
        update={
            "inputs": tuple(definitions),
            "projected_inputs": (),
            "ambiguous_inputs": tuple(ambiguous),
            "steps": tuple(steps),
        }
    )
    cache[canonical] = materialized
    return materialized
