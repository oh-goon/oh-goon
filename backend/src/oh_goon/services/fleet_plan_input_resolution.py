from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanInvalid
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
from oh_goon.core.path_authority import RepositoryPathSpace, navigation_path
from oh_goon.engine.git import GitService
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


@dataclass(frozen=True, slots=True)
class InputResolutionContext:
    """External value authority used only while resolving canonical Goon inputs."""

    repository: Path | None = None
    environment: Mapping[str, str] | None = None
    git: GitService = GitService()


def resolve_input_sources(
    sources: tuple[PlanInputSource, ...],
    *,
    context: InputResolutionContext,
    input_name: str,
) -> tuple[bool, PlanInputValue, str]:
    """Return the first defined source value; no expressions or computation are supported."""

    for source in sources:
        found, value, origin = _resolve_source(source, context=context, input_name=input_name)
        if found:
            return True, value, origin
    return False, None, "default"


def _resolve_source(
    source: PlanInputSource,
    *,
    context: InputResolutionContext,
    input_name: str,
) -> tuple[bool, PlanInputValue, str]:
    if isinstance(source, EnvInputSource):
        environment = context.environment
        if environment is None:
            return False, None, f"env:{source.name}"
        value = environment.get(source.name)
        if value is None or value == "":
            return False, None, f"env:{source.name}"
        return True, value, f"env:{source.name}"

    if isinstance(source, YamlInputSource):
        repository = _require_repository(context, input_name=input_name, source="yaml")
        path = RepositoryPathSpace.from_root(repository).resolve(
            source.path,
            label=f"Fleet input {input_name!r} YAML source",
            kind="file",
            require_exists=False,
        )
        if not path.exists():
            return False, None, f"yaml:{source.path}#{source.key}"
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} cannot read YAML source {source.path}: {exc}"
            ) from exc
        value = _mapping_key(payload, source.key)
        if value is _MISSING or value is None or value == "":
            return False, None, f"yaml:{source.path}#{source.key}"
        if type(value) not in (str, bool, int):
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} YAML source {source.path}#{source.key} "
                "must resolve to a scalar"
            )
        return True, value, f"yaml:{source.path}#{source.key}"

    if isinstance(source, TomlInputSource):
        repository = _require_repository(context, input_name=input_name, source="toml")
        path = RepositoryPathSpace.from_root(repository).resolve(
            source.path,
            label=f"Fleet input {input_name!r} TOML source",
            kind="file",
            require_exists=False,
        )
        if not path.exists():
            return False, None, f"toml:{source.path}#{source.key}"
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} cannot read TOML source {source.path}: {exc}"
            ) from exc
        value = _mapping_key(payload, source.key)
        if value is _MISSING or value is None or value == "":
            return False, None, f"toml:{source.path}#{source.key}"
        if type(value) not in (str, bool, int):
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} TOML source {source.path}#{source.key} "
                "must resolve to a scalar"
            )
        return True, value, f"toml:{source.path}#{source.key}"

    if isinstance(source, TextInputSource):
        repository = _require_repository(context, input_name=input_name, source="text")
        path = RepositoryPathSpace.from_root(repository).resolve(
            source.path,
            label=f"Fleet input {input_name!r} text source",
            kind="file",
            require_exists=False,
        )
        if not path.exists():
            return False, None, f"text:{source.path}"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} cannot read text source {source.path}: {exc}"
            ) from exc
        if not value:
            return False, None, f"text:{source.path}"
        if "\n" in value or "\r" in value:
            raise FleetPlanInvalid(
                f"Fleet input {input_name!r} text source {source.path} "
                "must contain exactly one non-empty line"
            )
        return True, value, f"text:{source.path}"

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
                return False, None, f"git:{source.field}"
            value = head if source.field == "commit_sha" else head[:8]
            return True, value, f"git:{source.field}"
        if source.field == "branch":
            if observation.branch is None:
                return False, None, "git:branch"
            return True, observation.branch, "git:branch"
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
    plan_root: bool = False,
    _cache: dict[Path, CompiledExecutionTemplate] | None = None,
) -> CompiledExecutionTemplate:
    """Materialize explicit projected inputs without copying child schemas in YAML."""

    cache = {} if _cache is None else _cache
    canonical = navigation_path(path)
    cached = cache.get(canonical)
    if cached is not None:
        return cached
    compiled = source_graph.node(canonical).compiled
    if not compiled.projected_inputs:
        cache[canonical] = compiled
        return compiled
    if compiled.inputs:
        raise FleetPlanInvalid(
            "Execution template cannot mix projected and explicitly defined inputs"
        )

    steps = list(compiled.steps)
    definitions: list[PlanInputDefinition] = []
    for name in compiled.projected_inputs:
        if plan_root:
            matches: list[tuple[int, CompiledGoonImportStep, PlanInputDefinition]] = []
            for index, step in enumerate(steps):
                if not isinstance(step, CompiledGoonImportStep):
                    continue
                child_node = source_graph.goon(canonical, step.goon)
                child = materialize_projected_inputs(source_graph, child_node.path, _cache=cache)
                definition = next((item for item in child.inputs if item.name == name), None)
                if definition is not None:
                    matches.append((index, step, definition))
            if not matches:
                raise FleetPlanInputRejected(
                    f"Plan projected input {name!r} is not owned by any directly selected Goon",
                    diagnostic_code="UNOWNED_PROJECTED_INPUT",
                    input_names=(name,),
                )
            if len(matches) != 1:
                owner_names = ", ".join(item[1].goon for item in matches)
                raise FleetPlanInputRejected(
                    f"Plan projected input {name!r} is ambiguous across direct Goons: "
                    f"{owner_names}; "
                    "bind it explicitly or expose it through one Composite Goon",
                    diagnostic_code="AMBIGUOUS_PROJECTED_INPUT",
                    input_names=(name,),
                )
            index, step, definition = matches[0]
            if any(item.name == name for item in step.inputs):
                raise FleetPlanInvalid(
                    f"Plan projected input {name!r} is already explicitly bound on {step.goon}"
                )
            definitions.append(definition)
            steps[index] = step.model_copy(
                update={
                    "inputs": (
                        *step.inputs,
                        PlanInputBinding(name=name, value=f"${{{{ inputs.{name} }}}}"),
                    )
                }
            )
            continue

        bound_contracts: list[tuple[str, PlanInputDefinition]] = []
        expected_reference = f"${{{{ inputs.{name} }}}}"
        for step in steps:
            if not isinstance(step, CompiledGoonImportStep):
                continue
            child_node = source_graph.goon(canonical, step.goon)
            child = materialize_projected_inputs(source_graph, child_node.path, _cache=cache)
            for binding in step.inputs:
                if binding.sources or binding.value != expected_reference:
                    continue
                definition = next(
                    (item for item in child.inputs if item.name == binding.name),
                    None,
                )
                if definition is None:
                    raise FleetPlanInvalid(
                        f"Composite Goon projected input {name!r} binds unknown child input "
                        f"{binding.name!r} on {step.goon}"
                    )
                bound_contracts.append((step.goon, definition))
        if not bound_contracts:
            raise FleetPlanInvalid(
                f"Composite Goon projected input {name!r} must be explicitly forwarded with: "
                f"${{{{ inputs.{name} }}}}"
            )
        first_owner, first = bound_contracts[0]
        first_contract = first.model_dump(mode="python", exclude={"name"})
        incompatible = [
            owner
            for owner, definition in bound_contracts[1:]
            if definition.model_dump(mode="python", exclude={"name"}) != first_contract
        ]
        if incompatible:
            owners = ", ".join([first_owner, *incompatible])
            raise FleetPlanInvalid(
                f"Composite Goon projected input {name!r} has incompatible child contracts: "
                f"{owners}"
            )
        definitions.append(first.model_copy(update={"name": name}))

    materialized = compiled.model_copy(
        update={"inputs": tuple(definitions), "projected_inputs": (), "steps": tuple(steps)}
    )
    cache[canonical] = materialized
    return materialized
