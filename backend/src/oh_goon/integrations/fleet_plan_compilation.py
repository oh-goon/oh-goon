from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol, cast

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from oh_goon.core.errors import FleetExtensionInvalid, FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    ExtensionCatalog,
)
from oh_goon.core.path_authority import ContainedPathSpace, navigation_path
from oh_goon.core.template_identity import canonical_template_key, import_step_id
from oh_goon.integrations.fleet_plan_goon_authoring import (
    apply_goon_tool_authority,
    normalize_goon_authoring_header,
)

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_plan_source_snapshot import FleetPlanSourceSnapshot


@dataclass(frozen=True, slots=True)
class ParsedFleetYamlDocument:
    """One syntax parse shared by compilation and source-position provenance."""

    payload: dict[str, Any]
    root: MappingNode


def parse_fleet_yaml_document(content: str, *, source: str) -> ParsedFleetYamlDocument:
    """Parse one Plan/Goon YAML document exactly once with the safe loader."""

    loader = yaml.SafeLoader(content)
    try:
        root = loader.get_single_node()
        if not isinstance(root, MappingNode):
            raise FleetPlanInvalid(f"YAML {source} must contain one top-level mapping")
        _reject_duplicate_yaml_keys(root, source=source)
        payload = loader.construct_document(root)
    except yaml.YAMLError as exc:
        raise FleetPlanInvalid(f"Cannot read YAML {source}: {exc}") from exc
    finally:
        loader.dispose()
    if not isinstance(payload, dict):  # defensive: MappingNode should always construct a mapping
        raise FleetPlanInvalid(f"YAML {source} must contain one top-level mapping")
    return ParsedFleetYamlDocument(payload=payload, root=root)


def _reject_duplicate_yaml_keys(node: MappingNode | SequenceNode, *, source: str) -> None:
    """Reject silent YAML mapping overrides before constructing the document."""

    if isinstance(node, MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode):
                key = key_node.value
                if key in seen:
                    line = key_node.start_mark.line + 1
                    column = key_node.start_mark.column + 1
                    raise FleetPlanInvalid(
                        f"Invalid YAML {source}: duplicate key {key!r} at line {line}, column {column}"
                    )
                seen.add(key)
            if isinstance(value_node, (MappingNode, SequenceNode)):
                _reject_duplicate_yaml_keys(value_node, source=source)
        return
    for item in node.value:
        if isinstance(item, (MappingNode, SequenceNode)):
            _reject_duplicate_yaml_keys(item, source=source)


def compile_goon_document(
    document: ParsedFleetYamlDocument,
    *,
    source: str = "goon.yml",
    expected_name: str | None = None,
) -> CompiledExecutionTemplate:
    return _compile_goon_payload(document.payload, source=source, expected_name=expected_name)


def compile_plan_document(
    document: ParsedFleetYamlDocument,
    *,
    source: str = "Fleet YAML plan",
    expected_name: str | None = None,
) -> CompiledExecutionTemplate:
    return _compile_plan_payload(document.payload, source=source, expected_name=expected_name)


class GoonTemplateProvider(Protocol):
    """Discover/resolve shipped Goons by one canonical category/name identity."""

    def keys(self) -> tuple[str, ...]: ...

    def source(self, template_key: str) -> FleetPlanSourceSnapshot: ...


def load_plan_template(path: Path) -> CompiledExecutionTemplate:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FleetPlanInvalid(f"Cannot read YAML {path}: {exc}") from exc
    return parse_plan_template(content, source=str(path))


def parse_goon_template(
    content: str, *, source: str = "goon.yml", expected_name: str | None = None
) -> CompiledExecutionTemplate:
    """Parse and compile one strict Goon document."""

    return compile_goon_document(
        parse_fleet_yaml_document(content, source=source),
        source=source,
        expected_name=expected_name,
    )


def _compile_goon_payload(
    payload: dict[str, Any], *, source: str, expected_name: str | None = None
) -> CompiledExecutionTemplate:
    """Compile an already-parsed Goon mapping without reinterpreting YAML syntax."""

    payload = dict(payload)
    authored_name, authored_description, authored_tools = normalize_goon_authoring_header(
        payload, source=source, expected_name=expected_name
    )
    if expected_name is None:
        execution = payload.get("execution")
        if execution is None:
            payload["execution"] = {"purpose": "verification"}
        elif isinstance(execution, dict):
            execution = dict(execution)
            execution.setdefault("purpose", "verification")
            payload["execution"] = execution
    if payload.get("requirements") == []:
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: omit empty requirements; declare only requirements that add execution facts"
        )
    payload.setdefault("requirements", [])
    if not isinstance(payload.get("requirements"), list):
        raise FleetPlanInvalid(f"Invalid Goon {source}: requirements must be a list when declared")
    forms = [key for key in ("steps", "goons") if key in payload]
    if len(forms) != 1:
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: an Atomic Goon declares steps and a Composite Goon "
            "declares goons; declare exactly one, never both"
        )
    allowed = {
        "version",
        "execution",
        "requirements",
        "inputs",
        "env",
        "outputs",
        "uv",
        "steps",
        "goons",
    }
    _reject_unknown_fields(payload, allowed=allowed, source=source, kind="Goon")
    normalized = {**payload, "name": authored_name, "description": authored_description}
    if forms[0] == "steps":
        _validate_authored_steps(normalized.get("steps"), source=source)
    raw_inputs = normalized.get("inputs")
    if forms[0] == "steps" and isinstance(raw_inputs, list):
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: Atomic Goon inputs own their schema and must be a mapping"
        )
    if forms[0] == "goons" and raw_inputs is not None:
        if (
            not isinstance(raw_inputs, list)
            or not raw_inputs
            or not all(isinstance(item, str) and item for item in raw_inputs)
        ):
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: Composite Goon inputs must be a non-empty list of exposed child input names; "
                "child Goons remain schema owners"
            )
        normalized["projected_inputs"] = raw_inputs
        normalized.pop("inputs")
    if forms[0] == "goons":
        if normalized.get("requirements"):
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: Composite Goons leave repository requirements "
                "with child Goons"
            )
        if "uv" in normalized:
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: uv belongs to an Atomic Goon execution environment, "
                "not a Composite Goon"
            )
        normalized["steps"] = _compile_goon_imports(
            normalized.pop("goons"),
            source=source,
            owner="Goon",
            projected_inputs=tuple(normalized.get("projected_inputs", ())),
        )
    _normalize_template_fields(normalized)
    apply_goon_tool_authority(normalized, tools=authored_tools, source=source)
    template = _compile_execution_template(normalized, source=source, kind="Goon")
    if forms[0] == "steps" and any(
        isinstance(step, CompiledGoonImportStep) for step in template.steps
    ):
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: Atomic Goon steps cannot compose Goons; "
            "use the top-level goons list to create a Composite Goon"
        )
    return template


def parse_plan_template(
    content: str,
    *,
    source: str = "Fleet YAML plan",
    expected_name: str | None = None,
) -> CompiledExecutionTemplate:
    """Parse and compile the enforced Plan authoring form."""

    return compile_plan_document(
        parse_fleet_yaml_document(content, source=source),
        source=source,
        expected_name=expected_name,
    )


def _compile_plan_payload(
    payload: dict[str, Any], *, source: str, expected_name: str | None = None
) -> CompiledExecutionTemplate:
    """Compile an already-parsed Plan mapping without reinterpreting YAML syntax."""

    payload = dict(payload)
    execution = payload.get("execution")
    if execution is None:
        payload["execution"] = {"purpose": "verification"}
    elif isinstance(execution, dict):
        execution = dict(execution)
        execution.setdefault("purpose", "verification")
        payload["execution"] = execution
    forbidden = [key for key in ("steps", "run", "command", "script", "imports") if key in payload]
    if forbidden:
        fields = ", ".join(forbidden)
        raise FleetPlanInvalid(
            f"Invalid Plan {source}: Plans cannot declare executable work ({fields}). "
            "Executable behavior belongs in a Goon. Create or import a Goon and reference it from this Plan."
        )
    allowed = {"version", "execution", "inputs", "goons"}
    _reject_unknown_fields(payload, allowed=allowed, source=source, kind="Plan")
    raw_goons = payload.get("goons")
    if raw_goons is None:
        raise FleetPlanInvalid(
            f"Invalid Plan {source}: Plans select and order Goons with a non-empty goons list"
        )
    normalized = dict(payload)
    normalized["authoring_kind"] = "plan"
    normalized["name"] = expected_name or "plan"
    normalized["description"] = ""
    raw_inputs = normalized.get("inputs")
    if raw_inputs is not None:
        if (
            not isinstance(raw_inputs, list)
            or not raw_inputs
            or not all(isinstance(item, str) and item for item in raw_inputs)
        ):
            raise FleetPlanInvalid(
                f"Invalid Plan {source}: inputs must be a non-empty list of projected Goon input names; "
                "Plans do not own input schemas"
            )
        normalized["projected_inputs"] = raw_inputs
        normalized.pop("inputs")
    normalized["steps"] = _compile_goon_imports(
        normalized.pop("goons"),
        source=source,
        owner="Plan",
        projected_inputs=tuple(normalized.get("projected_inputs", ())),
    )
    normalized["requirements"] = []
    _normalize_template_fields(normalized)
    return _compile_execution_template(normalized, source=source, kind="Plan")


def _reject_unknown_fields(
    payload: dict[str, Any], *, allowed: set[str], source: str, kind: str
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise FleetPlanInvalid(f"Invalid {kind} {source}: unsupported fields: {', '.join(unknown)}")


_PASS_INPUT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
# The alias names one exact import occurrence, not a package key or another resolver.
# Match the existing 40-character compiled import Step ID contract.
_IMPORT_ALIAS_NAME = re.compile(r"[a-z](?:[a-z0-9]|-[a-z0-9])*\Z")
_IMPORT_ALIAS_RESERVED = frozenset(
    {
        "goons",
        "steps",
        "inputs",
        "outputs",
        "self",
        "true",
        "false",
        "null",
        "yes",
        "no",
        "on",
        "off",
    }
)


def _compile_goon_imports(
    raw: Any, *, source: str, owner: str, projected_inputs: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise FleetPlanInvalid(f"Invalid {owner} {source}: goons must be a non-empty ordered list")
    occurrences: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    authored_aliases: dict[str, int] = {}
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            raw_key = item
            source_root = None
            bindings: Any = {}
            pass_names: tuple[str, ...] = ()
            failure_impact = "blocking"
            authored_alias: str | None = None
        elif isinstance(item, dict) and "import" in item:
            allowed = {"import", "with", "failure_impact", "as", "pass", "from", "phase"}
            unknown = sorted(set(item) - allowed)
            if unknown:
                raise FleetPlanInvalid(
                    f"Invalid {owner} {source}: Goon import {index} has unsupported fields: {', '.join(unknown)}"
                )
            authored_alias = item.get("as") if "as" in item else None
            if "as" in item:
                if (
                    type(authored_alias) is not str
                    or len(authored_alias) > 40
                    or _IMPORT_ALIAS_NAME.fullmatch(authored_alias) is None
                    or authored_alias in _IMPORT_ALIAS_RESERVED
                ):
                    raise FleetPlanInvalid(
                        f"Invalid {owner} {source}: Goon import {index} as: must be a "
                        "lowercase hyphenated occurrence ID, 1..40 characters, "
                        "not a reserved reference namespace"
                    )
                if authored_alias in authored_aliases:
                    raise FleetPlanInvalid(
                        f"Invalid {owner} {source}: duplicate explicit Goon import alias "
                        f"{authored_alias!r} at occurrences {authored_aliases[authored_alias]} and {index}"
                    )
                authored_aliases[authored_alias] = index
            raw_key = cast(str, item.get("import"))
            source_root = item.get("from")
            if "from" in item and (
                not isinstance(source_root, str)
                or not source_root.strip()
                or len(source_root) > 240
                or "${{" in source_root
                or "\x00" in source_root
                or "\\" in source_root
                or PurePosixPath(source_root).is_absolute()
            ):
                raise FleetPlanInvalid(
                    "Goon import from: must be a static relative catalog directory"
                )
            bindings = item.get("with", {})
            failure_impact = item.get("failure_impact", "blocking")
            pass_names = ()
        else:
            raise FleetPlanInvalid(
                f"Invalid {owner} {source}: goons item {index} must be category/name or a mapping with import: category/name"
            )
        try:
            key = canonical_template_key(raw_key, kind="Goon")
        except FleetPlanInvalid as exc:
            raise FleetPlanInvalid(f"Invalid {owner} {source}: {exc}") from exc
        if not isinstance(bindings, dict):
            raise FleetPlanInvalid(
                f"Invalid {owner} {source}: Goon import {key!r} with must be a mapping"
            )
        if isinstance(item, dict) and "pass" in item:
            label = f"Invalid {owner} {source}: Goon import {index} {key!r}"
            raw_pass = item["pass"]
            if not isinstance(raw_pass, list) or not raw_pass:
                raise FleetPlanInvalid(f"{label}: pass must be a nonempty list of input names")
            names: list[str] = []
            seen: set[str] = set()
            for ordinal, name in enumerate(raw_pass, start=1):
                if type(name) is not str or _PASS_INPUT_NAME.fullmatch(name) is None:
                    raise FleetPlanInvalid(
                        f"{label}: pass item {ordinal} is not a valid input name"
                    )
                if name in seen:
                    raise FleetPlanInvalid(f"{label}: duplicate pass input {name!r}; remove one")
                seen.add(name)
                if name not in projected_inputs and (owner != "Plan" or projected_inputs):
                    raise FleetPlanInvalid(
                        f"{label}: inputs does not declare {name!r}; "
                        "expose it in inputs: or remove this pass"
                    )
                if name in bindings:
                    raise FleetPlanInvalid(
                        f"{label}: duplicate child input binding {name!r} in pass and with; remove one"
                    )
                names.append(name)
            pass_names = tuple(names)
        compiled_bindings = [
            _input_binding_payload(name, f"${{{{ inputs.{name} }}}}") for name in pass_names
        ]
        compiled_bindings.extend(
            _input_binding_payload(name, value) for name, value in bindings.items()
        )
        occurrences[key] = occurrences.get(key, 0) + 1
        compiled_id = (
            authored_alias if authored_alias is not None else import_step_id(key, occurrences[key])
        )
        collision = next(
            (
                ordinal
                for ordinal, prior in enumerate(result, start=1)
                if prior["step_id"] == compiled_id
            ),
            None,
        )
        if collision is not None:
            raise FleetPlanInvalid(
                f"Invalid {owner} {source}: Goon import {index} occurrence ID {compiled_id!r} "
                f"collides with import {collision}; choose a unique as: alias "
                "or keep the generated ID"
            )
        result.append(
            {
                "kind": "goon",
                "step_id": compiled_id,
                "goon": key,
                "source_root": source_root,
                "phase": item.get("phase") if isinstance(item, dict) else None,
                "failure_impact": failure_impact,
                "inputs": compiled_bindings,
            }
        )
    return result


def _normalize_template_fields(normalized: dict[str, Any]) -> None:
    raw_inputs = normalized.get("inputs")
    if isinstance(raw_inputs, dict):
        normalized["inputs"] = [
            {"name": name, **(_input_definition_payload(value))}
            for name, value in raw_inputs.items()
        ]
    raw_env = normalized.get("env")
    if isinstance(raw_env, dict):
        normalized["env"] = [
            {"name": name, "source": _env_source_payload(value)} for name, value in raw_env.items()
        ]
    raw_outputs = normalized.get("outputs")
    if isinstance(raw_outputs, dict):
        normalized["outputs"] = [
            {"name": name, "source": _module_output_source(value)}
            for name, value in raw_outputs.items()
        ]
    raw_steps = normalized.get("steps")
    if isinstance(raw_steps, list):
        normalized_steps, inclusions = _normalize_steps(raw_steps)
        normalized["steps"] = normalized_steps
        normalized["inclusions"] = inclusions
    _apply_uv_environment(normalized)


def _compile_execution_template(
    normalized: dict[str, Any], *, source: str, kind: str
) -> CompiledExecutionTemplate:
    try:
        return CompiledExecutionTemplate.model_validate(normalized)
    except ValidationError as exc:
        raise FleetPlanInvalid(f"Invalid {kind} {source}: {exc}") from exc


def load_extension_catalog(path: Path) -> ExtensionCatalog:
    path = navigation_path(path)
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FleetExtensionInvalid(f"Cannot read Fleet extension catalog {path}: {exc}") from exc
    try:
        return ExtensionCatalog.model_validate(payload)
    except ValidationError as exc:
        raise FleetExtensionInvalid(f"Invalid Fleet extension catalog {path}: {exc}") from exc


def contained_plan_file(root: Path, base: Path, value: str, *, kind: str) -> Path:
    return ContainedPathSpace.from_root(root).resolve_from(base, value, label=f"{kind} path")


def qualified_step_id(prefix: str, step_id: str) -> str:
    value = f"{prefix}.{step_id}" if prefix else step_id
    if len(value) > 80:
        raise FleetPlanInvalid(
            f"Resolved Fleet step_id exceeds 80 characters after template composition: {value}"
        )
    return value


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FleetPlanInvalid(f"Cannot read YAML {path}: {exc}") from exc
    return parse_yaml_mapping(content, source=str(path))


def parse_yaml_mapping(content: str, *, source: str) -> dict[str, Any]:
    return parse_fleet_yaml_document(content, source=source).payload


def _published_output_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"path": value}
    if isinstance(value, dict):
        return dict(value)
    raise FleetPlanInvalid("Fleet plan output declarations must be a path string or mapping")


def _input_definition_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {"required": True}
    if type(value) in (str, bool, int):
        return {"default": value}
    if isinstance(value, dict):
        payload = dict(value)
        if "required" in payload:
            raise FleetPlanInvalid(
                "Fleet YAML inputs do not author required:; omit default for required inputs or use optional: true"
            )
        optional = payload.pop("optional", False)
        if type(optional) is not bool:
            raise FleetPlanInvalid("Fleet YAML input optional must be true or false")
        if optional and "default" in payload:
            raise FleetPlanInvalid("Fleet YAML optional inputs cannot also declare a default")
        payload["required"] = not optional and "default" not in payload
        if "type" in payload:
            payload["input_type"] = payload.pop("type")
        if "min" in payload:
            payload["minimum"] = payload.pop("min")
        if "max" in payload:
            payload["maximum"] = payload.pop("max")
        return payload
    raise FleetPlanInvalid(
        "Fleet YAML input definitions must be mappings, scalar defaults, or null"
    )


def _input_binding_payload(name: Any, value: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not name:
        raise FleetPlanInvalid("Fleet Goon input binding names must be non-empty strings")
    if not isinstance(value, dict) or set(value) != {"from"}:
        return {"name": name, "value": value}
    raw_sources = value["from"]
    sources = raw_sources if isinstance(raw_sources, list) else [raw_sources]
    if not sources:
        raise FleetPlanInvalid(f"Fleet Goon input {name!r} from must contain at least one source")
    return {"name": name, "sources": [_input_source_payload(item, name=name) for item in sources]}


def _input_source_payload(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) != 1:
        raise FleetPlanInvalid(
            f"Fleet Goon input {name!r} sources must be one-key mappings using "
            "env, yaml, toml, text, or git"
        )
    if "env" in value:
        env_name = value["env"]
        if not isinstance(env_name, str) or not env_name:
            raise FleetPlanInvalid(f"Fleet Goon input {name!r} env source must name one variable")
        return {"kind": "env", "name": env_name}
    if "yaml" in value:
        spec = value["yaml"]
        if not isinstance(spec, dict) or set(spec) != {"path", "key"}:
            raise FleetPlanInvalid(
                f"Fleet Goon input {name!r} yaml source must contain exactly path and key"
            )
        return {"kind": "yaml", "path": spec["path"], "key": spec["key"]}
    if "toml" in value:
        spec = value["toml"]
        if not isinstance(spec, dict) or set(spec) != {"path", "key"}:
            raise FleetPlanInvalid(
                f"Fleet Goon input {name!r} toml source must contain exactly path and key"
            )
        return {"kind": "toml", "path": spec["path"], "key": spec["key"]}
    if "text" in value:
        spec = value["text"]
        if not isinstance(spec, dict) or set(spec) != {"path"}:
            raise FleetPlanInvalid(
                f"Fleet Goon input {name!r} text source must contain exactly path"
            )
        return {"kind": "text", "path": spec["path"]}
    if "git" in value:
        return {"kind": "git", "field": value["git"]}
    raise FleetPlanInvalid(
        f"Fleet Goon input {name!r} source must use env, yaml, toml, text, or git"
    )


def _env_source_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FleetPlanInvalid("Fleet env definitions must be mappings")
    payload = dict(value)
    if set(payload) == {"from"} and payload.get("from") == "host":
        return {"kind": "host"}
    if set(payload) == {"value"} and type(payload.get("value")) in (str, bool, int):
        return {"kind": "literal", "value": payload["value"]}
    raise FleetPlanInvalid("Fleet env definitions must use {from: host} or {value: ...}")


def _module_output_source(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and set(value) == {"from"} and isinstance(value["from"], str):
        return value["from"]
    raise FleetPlanInvalid(
        "Goon outputs must be a Step output reference or a mapping containing only 'from'"
    )


def _validate_authored_steps(raw_steps: Any, *, source: str) -> None:
    if not isinstance(raw_steps, list):
        return
    typed_uses = {
        "agent",
        "git",
        "pre_commit",
        "oci_build",
        "oci_publish",
        "oci_tag_assert",
        "kubernetes_set_image",
        "kubernetes_rollout_verify",
        "extension",
        "dependency_inventory",
    }
    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        internal = sorted(set(item) & {"argv", "kind", "step_id"})
        if internal:
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: Step {index + 1} contains internal execution fields: {', '.join(internal)}"
            )
        uses = item.get("uses")
        if uses is not None and uses not in typed_uses:
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: Step {index + 1} has unsupported typed executor: {uses}"
            )
        if uses == "git" and item.get("operation") == "native":
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: Step {index + 1} must omit operation: native; args already select native Git"
            )
        if "run" in item and "timeout_seconds" in item:
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: Step {index + 1} declares a command timeout; normal run: Steps use the backend default"
            )
        capabilities = item.get("capabilities")
        if isinstance(capabilities, dict) and "tools" in capabilities:
            raise FleetPlanInvalid(
                f"Invalid Goon {source}: Step {index + 1} must choose top-level tools.allow; "
                "capabilities.tools is an internal compiled field"
            )


def _normalize_steps(raw_steps: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    explicit_ids = {
        str(item["step_id"])
        for item in raw_steps
        if isinstance(item, dict) and isinstance(item.get("step_id"), str) and item.get("step_id")
    }
    used_ids = set(explicit_ids)
    normalized_steps: list[Any] = []
    inclusions: list[dict[str, Any]] = []
    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            normalized_steps.append(_normalize_step_mapping(item))
            continue
        raw = dict(item)
        include_when = raw.pop("include_when", None)
        step = _normalize_step_mapping(raw)
        if isinstance(step, dict) and not step.get("step_id"):
            step["step_id"] = _generated_step_id(step, index=index, used=used_ids)
        if isinstance(step, dict) and isinstance(step.get("step_id"), str):
            used_ids.add(step["step_id"])
        if include_when is not None:
            if not isinstance(step, dict) or not isinstance(step.get("step_id"), str):
                raise FleetPlanInvalid("Fleet include_when requires a resolvable step identity")
            if not isinstance(include_when, dict):
                raise FleetPlanInvalid("Fleet include_when must be a mapping")
            inclusions.append({"step_id": step["step_id"], **dict(include_when)})
        normalized_steps.append(step)
    return normalized_steps, inclusions


def _generated_step_id(step: dict[str, Any], *, index: int, used: set[str]) -> str:
    base = _generated_step_base(step, index=index)
    candidate = base
    occurrence = 2
    while candidate in used:
        suffix = f"-{occurrence}"
        candidate = f"{base[: 80 - len(suffix)]}{suffix}"
        occurrence += 1
    return candidate


def _generated_step_base(step: dict[str, Any], *, index: int) -> str:
    kind = step.get("kind")
    argv = step.get("argv")
    token: str | None = None
    if kind == "command" and isinstance(argv, (list, tuple)) and argv:
        values = [str(item) for item in argv]
        if len(values) >= 3 and values[:2] == ["uv", "run"]:
            token = values[2]
        else:
            token = values[0]
    elif isinstance(kind, str):
        token = kind
    raw = token.rsplit("/", 1)[-1] if token else f"step-{index}"
    base = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower() or f"step-{index}"
    return base[:80]


_RUN_EXPRESSION = re.compile(r"\$\{\{.*?\}\}")
_RUN_SHELL_PUNCTUATION = frozenset({"|", "||", "&", "&&", ";", "<", ">", "<<", ">>"})


def _parse_run_argv(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        raise FleetPlanInvalid("Fleet run must be one non-empty command string")
    if "\n" in value or "\r" in value:
        raise FleetPlanInvalid("Fleet run cannot contain multiline shell")
    if "$(" in value or "`" in value:
        raise FleetPlanInvalid("Fleet run cannot contain shell command substitution")

    expressions: list[str] = []

    def replace_expression(match: re.Match[str]) -> str:
        expressions.append(match.group(0))
        return f"__OH_GOON_EXPR_{len(expressions) - 1}__"

    protected = _RUN_EXPRESSION.sub(replace_expression, value)
    lexer = shlex.shlex(protected, posix=True, punctuation_chars="|&;<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        argv = list(lexer)
    except ValueError as exc:
        raise FleetPlanInvalid(f"Fleet run has invalid quoting: {exc}") from exc
    if not argv:
        raise FleetPlanInvalid("Fleet run must resolve to at least one argv token")
    if any(token in _RUN_SHELL_PUNCTUATION or set(token) <= set("|&;<>") for token in argv):
        raise FleetPlanInvalid(
            "Fleet run executes without a shell; &&, ||, pipes, redirects, and ';' are not supported"
        )
    for index, expression in enumerate(expressions):
        placeholder = f"__OH_GOON_EXPR_{index}__"
        argv = [token.replace(placeholder, expression) for token in argv]
    executable = argv[0].rsplit("/", 1)[-1].lower()
    if (
        len(argv) >= 2
        and executable in {"python", "python3", "bash", "sh", "zsh"}
        and argv[1] == "-c"
    ):
        raise FleetPlanInvalid(
            "Fleet run cannot embed a shell/Python program with -c; expose complex logic as a real project command"
        )
    if len(argv) >= 4 and argv[:2] == ["uv", "run"]:
        nested = argv[2].rsplit("/", 1)[-1].lower()
        if nested in {"python", "python3", "bash", "sh", "zsh"} and argv[3] == "-c":
            raise FleetPlanInvalid(
                "Fleet run cannot embed a shell/Python program with -c; expose complex logic as a real project command"
            )
    return argv


def _apply_uv_environment(normalized: dict[str, Any]) -> None:
    raw_uv = normalized.pop("uv", None)
    if raw_uv is None:
        return
    if (
        not isinstance(raw_uv, dict)
        or not set(raw_uv) <= {"group", "project"}
        or "group" not in raw_uv
    ):
        raise FleetPlanInvalid("Goon uv must contain group and may contain project")
    group = raw_uv.get("group")
    project = raw_uv.get("project", ".")
    if (
        not isinstance(group, str)
        or re.fullmatch(
            r"(?:[A-Za-z0-9][A-Za-z0-9._-]{0,63}|\$\{\{\s*inputs\.[A-Za-z_][A-Za-z0-9_-]*\s*\}\})",
            group,
        )
        is None
    ):
        raise FleetPlanInvalid("Goon uv.group has an invalid dependency-group name")
    if not isinstance(project, str) or not project.strip():
        raise FleetPlanInvalid("Goon uv.project must be a non-empty repository-relative path")
    project_path = PurePosixPath(project.replace("\\", "/"))
    if project_path.is_absolute() or ".." in project_path.parts:
        raise FleetPlanInvalid("Goon uv.project must be repository-relative")
    project = project_path.as_posix()
    steps = normalized.get("steps")
    if not isinstance(steps, list) or not steps:
        raise FleetPlanInvalid("Goon uv requires Atomic Goon command steps")
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "command":
            raise FleetPlanInvalid(
                "Goon uv can own only command Steps; typed executors keep their own model"
            )
        if "cache" in step:
            raise FleetPlanInvalid(
                "Goon uv cache lineage is backend-owned; remove authored Step cache"
            )
        argv = step.get("argv")
        if not isinstance(argv, list) or len(argv) < 3 or argv[:2] != ["uv", "run"]:
            raise FleetPlanInvalid("Goon uv Steps must use run: uv run <command>")
        if argv[2].startswith("-"):
            raise FleetPlanInvalid(
                "Goon uv owns uv-run options; keep authored run as 'uv run <command>'"
            )
        step["argv"] = [
            "uv",
            "run",
            "--frozen",
            "--no-default-groups",
            "--project",
            project,
            "--group",
            group,
            *argv[2:],
        ]
        step["uv"] = {"project": project, "group": group}
        step.setdefault("working_directory", project)


def _normalize_step_mapping(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    authored_id = normalized.pop("id", None)
    if authored_id is not None:
        normalized["step_id"] = authored_id
    run = normalized.pop("run", None)
    if run is not None:
        conflicting = sorted(key for key in ("uses", "kind", "argv") if key in normalized)
        if conflicting:
            raise FleetPlanInvalid("Fleet run cannot be combined with " + ", ".join(conflicting))
        normalized["kind"] = "command"
        normalized["argv"] = _parse_run_argv(run)
    uses = normalized.pop("uses", None)
    if uses is not None:
        if "kind" in normalized and normalized["kind"] != uses:
            raise FleetPlanInvalid("Fleet YAML step cannot disagree between uses and kind")
        normalized["kind"] = uses
    if isinstance(normalized.get("outputs"), dict):
        normalized["outputs"] = [
            {"name": name, **_published_output_payload(value)}
            for name, value in normalized["outputs"].items()
        ]
    if isinstance(normalized.get("workspace"), dict):
        workspace = dict(normalized["workspace"])
        if "source" in workspace:
            if "from" in workspace:
                raise FleetPlanInvalid("Fleet workspace cannot declare both source and from")
            # Canonical model form is already explicit; Pydantic validates the discriminated source.
            normalized["workspace"] = workspace
        else:
            if "from" not in workspace:
                raise FleetPlanInvalid(
                    "Fleet workspace mapping must explicitly declare from: repository or from: step"
                )
            source = workspace.pop("from")
            if source == "repository":
                if "step" in workspace:
                    raise FleetPlanInvalid("Fleet workspace from: repository cannot declare step")
                workspace["source"] = {"kind": "repository"}
            elif source == "step":
                step_id = workspace.pop("step", None)
                if not isinstance(step_id, str) or not step_id:
                    raise FleetPlanInvalid(
                        "Fleet workspace from: step requires a non-empty step name"
                    )
                workspace["source"] = {"kind": "step", "step_id": step_id}
            else:
                raise FleetPlanInvalid("Fleet workspace from must be 'repository' or 'step'")
            normalized["workspace"] = workspace
    if normalized.get("kind") == "git":
        operation = normalized.pop("operation", None)
        if operation is None and "args" in normalized:
            operation = "native"
        if operation is not None:
            action = {"operation": operation}
            for key in ("ref", "branch", "start_point", "paths", "message", "allow_empty", "args"):
                if key in normalized:
                    action[key] = normalized.pop(key)
            normalized["action"] = action
    return normalized
