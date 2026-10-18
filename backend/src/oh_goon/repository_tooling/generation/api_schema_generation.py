from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from oh_goon.repository_tooling.generation.generated_typescript import pack_blocks

_PASCAL_TOKEN = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+")
_TS_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _discriminator_type_name(schema_name: str, property_name: str) -> str:
    schema_tokens = _PASCAL_TOKEN.findall(schema_name)
    property_tokens = [part[:1].upper() + part[1:] for part in property_name.split("_") if part]
    if property_name == "status" and schema_tokens[-1:] == ["State"]:
        schema_tokens = schema_tokens[:-1]
    overlap = 0
    for size in range(1, min(len(schema_tokens), len(property_tokens)) + 1):
        if schema_tokens[-size:] == property_tokens[:size]:
            overlap = size
    return "".join([*schema_tokens, *property_tokens[overlap:]])


def component_schemas(openapi: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return exactly the schemas declared by canonical OpenAPI components."""
    components = openapi.get("components")
    if not isinstance(components, dict):
        raise ValueError("OpenAPI components must be an object")
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        raise ValueError("OpenAPI components.schemas must be an object")
    if not all(
        isinstance(name, str) and isinstance(schema, dict) for name, schema in schemas.items()
    ):
        raise ValueError("OpenAPI component schemas must map names to schema objects")
    return dict(schemas)


def runtime_enums(openapi: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Derive runtime constants from canonical enum/discriminator facts without creating schemas."""
    enums: dict[str, tuple[str, ...]] = {}
    for schema_name, schema in sorted(component_schemas(openapi).items()):
        values = schema.get("enum")
        if isinstance(values, list) and values and all(isinstance(value, str) for value in values):
            enums[schema_name] = tuple(values)

        discriminator = schema.get("discriminator")
        if not isinstance(discriminator, dict):
            continue
        property_name = discriminator.get("propertyName")
        mapping = discriminator.get("mapping")
        if not isinstance(property_name, str) or not isinstance(mapping, dict) or not mapping:
            continue
        discriminator_values = tuple(sorted(value for value in mapping if isinstance(value, str)))
        if len(discriminator_values) != len(mapping):
            raise ValueError(f"Discriminator mapping keys must be strings for {schema_name}")
        enum_name = _discriminator_type_name(schema_name, property_name)
        existing = enums.get(enum_name)
        if existing is not None and set(existing) != set(discriminator_values):
            raise ValueError(f"Runtime enum collision for {enum_name}")
        enums[enum_name] = discriminator_values
    return enums


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def ts_type(schema: dict[str, Any]) -> str:
    if "const" in schema:
        return json.dumps(schema["const"])
    if "$ref" in schema:
        return f'components["schemas"]["{ref_name(schema["$ref"])}"]'
    if "anyOf" in schema:
        return " | ".join(ts_type(item) for item in schema["anyOf"])
    if "oneOf" in schema:
        return " | ".join(ts_type(item) for item in schema["oneOf"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    kind = schema.get("type")
    if kind == "string":
        return "string"
    if kind in {"integer", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        item_type = ts_type(schema.get("items", {}))
        return f"({item_type})[]" if " | " in item_type else f"{item_type}[]"
    if kind == "object" or "properties" in schema:
        return object_type(schema)
    return "unknown"


def object_type(schema: dict[str, Any], indent: str = "") -> str:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = ["{"]
    child_indent = indent + "  "
    for name, value in properties.items():
        optional = "" if name in required else "?"
        lines.append(f"{child_indent}{json.dumps(name)}{optional}: {ts_type(value)};")
    if schema.get("additionalProperties"):
        extra = schema["additionalProperties"]
        value_type = ts_type(extra) if isinstance(extra, dict) else "unknown"
        lines.append(f"{child_indent}[key: string]: {value_type};")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def schema_definition(name: str, schema: dict[str, Any]) -> str:
    rendered = ts_type(schema)
    if "\n" not in rendered:
        return f"    {name}: {rendered};"
    lines = rendered.splitlines()
    return "\n".join(
        [f"    {name}: {lines[0]}"]
        + [f"    {line}" for line in lines[1:-1]]
        + [f"    {lines[-1]};"]
    )


def openapi_digest(openapi: dict[str, Any]) -> str:
    canonical = json.dumps(openapi, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema_part_lines(name: str, schema: dict[str, Any]) -> list[str]:
    definition = schema_definition(name, schema)
    return definition.splitlines()


def render_schema_files(openapi: dict[str, Any]) -> dict[str, str]:
    schemas = component_schemas(openapi)
    digest = openapi_digest(openapi)
    shards = pack_blocks(
        (name, _schema_part_lines(name, schemas[name])) for name in sorted(schemas)
    )
    outputs: dict[str, str] = {}
    part_names: list[str] = []
    for index, shard in enumerate(shards):
        part_name = f"SchemaPart{index:03d}"
        part_names.append(part_name)
        lines = [
            f"// OPENAPI_SHA256: {digest}",
            "// AUTO-GENERATED OpenAPI component schema shard.",
            "// Do not edit this file by hand.",
            'import type { components } from "../schema";',
            "",
            f"export interface {part_name} {{",
        ]
        for _name, block in shard:
            lines.extend(block)
        lines.extend(["}", ""])
        outputs[f"schemaParts/schemaPart{index:03d}.d.ts"] = "\n".join(lines)

    root_lines = [
        f"// OPENAPI_SHA256: {digest}",
        "// AUTO-GENERATED FROM FastAPI/OpenAPI by backend/src/oh_goon/repository_tooling/generation/generate_api_types.py.",
        "// Canonical schema surface assembled from deterministic generated shards.",
        "// Do not edit this file by hand.",
        "",
    ]
    for index, part_name in enumerate(part_names):
        root_lines.append(
            f'import type {{ {part_name} }} from "./schemaParts/schemaPart{index:03d}";'
        )
    root_lines.extend(
        [
            "",
            "export interface components {",
            "  schemas: " + " & ".join(part_names) + ";",
            "}",
            "",
        ]
    )
    outputs["schema.d.ts"] = "\n".join(root_lines)
    return outputs


def render(openapi: dict[str, Any]) -> str:
    return render_schema_files(openapi)["schema.d.ts"]


def render_models(openapi: dict[str, Any]) -> str:
    schemas = component_schemas(openapi)
    lines = [
        f"// OPENAPI_SHA256: {openapi_digest(openapi)}",
        "// AUTO-GENERATED named frontend model aliases from canonical FastAPI/OpenAPI components.",
        "// Do not edit this file by hand.",
        'import type { components } from "./schema";',
        "",
    ]
    for name in sorted(schemas):
        if not _TS_IDENTIFIER.fullmatch(name):
            raise ValueError(f"OpenAPI schema name is not a TypeScript identifier: {name!r}")
        lines.append(f'export type {name} = components["schemas"]["{name}"];')
    lines.append("")
    return "\n".join(lines)


def enum_member_name(value: str) -> str:
    candidate = "".join(char if char.isalnum() else "_" for char in value).upper().strip("_")
    if not candidate:
        candidate = "VALUE"
    if candidate[0].isdigit():
        candidate = f"VALUE_{candidate}"
    return candidate


def _enum_lines(name: str, values: tuple[str, ...]) -> list[str]:
    lines = [f"export const {name} = {{"]
    seen: set[str] = set()
    for value in values:
        member = enum_member_name(value)
        if member in seen:
            raise ValueError(f"Enum key collision for {name}: {value}")
        seen.add(member)
        lines.append(f"  {member}: {json.dumps(value)},")
    return [*lines, "} as const;", ""]


def render_enums(openapi: dict[str, Any]) -> str:
    lines = [
        f"// OPENAPI_SHA256: {openapi_digest(openapi)}",
        "// AUTO-GENERATED runtime constants from canonical FastAPI/OpenAPI enum/discriminator facts.",
        "// Runtime conveniences are not added back into the schema/model namespace.",
        "// Do not edit this file by hand.",
        "",
    ]
    for name, values in sorted(runtime_enums(openapi).items()):
        lines.extend(_enum_lines(name, values))
    return "\n".join(lines)
