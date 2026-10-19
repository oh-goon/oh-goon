from __future__ import annotations

import re
from dataclasses import dataclass

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    HostEnvSource,
    LiteralEnvironmentPart,
    LiteralEnvSource,
    PlanEnvDefinition,
    ResolvedEnvironmentBinding,
    ResolvedEnvironmentPart,
)

_INTERPOLATION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
_REFERENCE = re.compile(r"^(inputs|env)\.([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass(frozen=True)
class CompiledPipelineValue:
    parts: tuple[ResolvedEnvironmentPart, ...]

    @property
    def has_host_source(self) -> bool:
        return any(isinstance(item, HostEnvironmentPart) for item in self.parts)

    def argv_template(self) -> str:
        if self.has_host_source:
            raise FleetPlanInvalid(
                "Host-sourced pipeline env cannot be interpolated into argv; "
                "import it into the step environment explicitly"
            )
        chunks: list[str] = []
        for part in self.parts:
            if isinstance(part, LiteralEnvironmentPart):
                chunks.append(part.value)
            else:  # pragma: no cover - guarded above
                raise AssertionError(f"Unhandled pipeline value part: {type(part).__name__}")
        return "".join(chunks)

    def trace_template(self) -> str:
        """Render value provenance without reading or exposing host values."""
        chunks: list[str] = []
        for part in self.parts:
            if isinstance(part, LiteralEnvironmentPart):
                chunks.append(part.value)
            elif isinstance(part, HostEnvironmentPart):
                chunks.append(f"<host:{part.name}:redacted>")
            else:  # pragma: no cover - closed union
                raise AssertionError(f"Unhandled pipeline value part: {type(part).__name__}")
        return "".join(chunks)


class PipelineValueCompiler:
    """Compile Goon-local inputs/env into explicit immutable dataflow."""

    def __init__(
        self,
        *,
        inputs: dict[str, PlanInputValue],
        env: tuple[PlanEnvDefinition, ...],
        qualify_step,
    ) -> None:
        self._inputs = inputs
        self._env = {item.name: item for item in env}
        self._qualify_step = qualify_step

    def validate_all(self) -> None:
        """Validate every declared pipeline value even when no step imports it."""
        for name in self._env:
            self._compile_env(name)

    def describe_env(self, name: str) -> CompiledPipelineValue:
        return self._compile_env(name)

    def render_authoring_text(self, value: str) -> str:
        """Render inputs/env in command-like strings before admission.

        Host-backed values are deliberately rejected here; they may only enter a
        subprocess through an explicit import.
        """

        cursor = 0
        result: list[str] = []
        for match in _INTERPOLATION.finditer(value):
            result.append(value[cursor : match.start()])
            expression = match.group(1).strip()
            if expression.startswith(("steps.", "goons.")):
                result.append(match.group(0))
            else:
                compiled = self.compile_expression(expression)
                result.append(compiled.argv_template())
            cursor = match.end()
        result.append(value[cursor:])
        return "".join(result)

    def compile_imports(
        self,
        *,
        env_names: tuple[str, ...],
    ) -> tuple[ResolvedEnvironmentBinding, ...]:
        return tuple(
            ResolvedEnvironmentBinding(name=name, parts=self._compile_env(name).parts)
            for name in env_names
        )

    def compile_expression(self, expression: str) -> CompiledPipelineValue:
        expression = expression.strip()
        reference = _REFERENCE.fullmatch(expression)
        if reference:
            namespace, name = reference.groups()
            if namespace == "inputs":
                return self._compile_input(name)
            return self._compile_env(name)
        raise FleetPlanInvalid(
            "Unsupported Fleet pipeline expression. Use inputs.NAME or env.NAME."
        )

    def _compile_input(self, name: str) -> CompiledPipelineValue:
        if name not in self._inputs:
            raise FleetPlanInvalid(
                f"Fleet pipeline expression references undeclared input {name!r}"
            )
        value = self._inputs[name]
        if value is None:
            return CompiledPipelineValue((LiteralEnvironmentPart(value=""),))
        return CompiledPipelineValue((LiteralEnvironmentPart(value=_scalar_text(value)),))

    def _compile_env(self, name: str) -> CompiledPipelineValue:
        try:
            definition = self._env[name]
        except KeyError as exc:
            raise FleetPlanInvalid(
                f"Fleet pipeline expression/import references undeclared env {name!r}"
            ) from exc
        source = definition.source
        if isinstance(source, HostEnvSource):
            return CompiledPipelineValue((HostEnvironmentPart(name=name),))
        if isinstance(source, LiteralEnvSource):
            return CompiledPipelineValue(
                (LiteralEnvironmentPart(value=_scalar_text(source.value)),)
            )
        raise AssertionError(f"Unhandled Fleet env source: {type(source).__name__}")

    def _compile_template(self, value: str) -> CompiledPipelineValue:
        parts: list[ResolvedEnvironmentPart] = []
        cursor = 0
        for match in _INTERPOLATION.finditer(value):
            if match.start() > cursor:
                parts.append(LiteralEnvironmentPart(value=value[cursor : match.start()]))
            parts.extend(self.compile_expression(match.group(1)).parts)
            cursor = match.end()
        if cursor < len(value):
            parts.append(LiteralEnvironmentPart(value=value[cursor:]))
        if not parts:
            parts.append(LiteralEnvironmentPart(value=""))
        return CompiledPipelineValue(tuple(parts))


def _scalar_text(value: str | bool | int) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)
