from __future__ import annotations

import json
import re
from typing import Any, Iterable

from oh_goon.repository_tooling.generation.api_operation_generation import (
    HTTP_METHODS,
    ApiOperation,
    extract_operations,
)
from oh_goon.repository_tooling.generation.api_schema_generation import (
    component_schemas,
    openapi_digest,
    ref_name,
)
from oh_goon.repository_tooling.generation.generated_typescript import pack_blocks


def _validator_name(schema_name: str) -> str:
    return f"validate{schema_name}"


def _indent(lines: Iterable[str], level: int = 1) -> list[str]:
    prefix = "  " * level
    return [f"{prefix}{line}" if line else "" for line in lines]


def _uses_identifier(lines: Iterable[str], identifier: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(identifier)}\b")
    return any(pattern.search(line) for line in lines)


def _validator_runtime_import(lines: Iterable[str], module: str) -> str | None:
    values = [
        name
        for name in ("fail", "isRecord", "validateReference", "validateUnion")
        if _uses_identifier(lines, name)
    ]
    items = [*values]
    if _uses_identifier(lines, "JsonRecord"):
        items.append("type JsonRecord")
    if not items:
        return None
    return f'import {{ {", ".join(items)} }} from "{module}";'


def _reference_validation(
    reference: str, value: str, path: str, contract: str, stack: str
) -> list[str]:
    if not reference.startswith("#/components/schemas/"):
        return [f"fail({contract}, {path}, {json.dumps(f'unsupported reference {reference}')});"]
    name = ref_name(reference)
    return [
        f"validateReference({json.dumps(name)}, {_validator_name(name)}, {value}, {path}, {contract}, {stack});"
    ]


def _union_validation(
    options: list[Any], value: str, path: str, contract: str, stack: str
) -> list[str]:
    lines = [f"validateUnion({contract}, {path}, ["]
    for option in options:
        if not isinstance(option, dict):
            continue
        lines.append("  () => {")
        lines.extend(
            _indent(
                _render_validation(option, value=value, path=path, contract=contract, stack=stack),
                2,
            )
        )
        lines.append("  },")
    lines.append("]);")
    return lines


def _const_enum_validation(
    schema: dict[str, Any], value: str, path: str, contract: str
) -> list[str]:
    lines: list[str] = []
    if "const" in schema:
        lines.append(
            f'if ({value} !== {json.dumps(schema["const"])}) fail({contract}, {path}, "did not match required constant");'
        )
    enum = schema.get("enum")
    if isinstance(enum, list):
        lines.append(
            f'if (!({json.dumps(enum)} as unknown[]).includes({value})) fail({contract}, {path}, "was not an allowed enum value");'
        )
    return lines


def _string_validation(schema: dict[str, Any], value: str, path: str, contract: str) -> list[str]:
    lines = [f'if (typeof {value} !== "string") fail({contract}, {path}, "expected string");']
    minimum, maximum = schema.get("minLength"), schema.get("maxLength")
    if isinstance(minimum, (int, float)):
        reason = json.dumps(f"must contain at least {minimum} characters")
        lines.append(
            f"if (({value} as string).length < {minimum}) fail({contract}, {path}, {reason});"
        )
    if isinstance(maximum, (int, float)):
        reason = json.dumps(f"must contain at most {maximum} characters")
        lines.append(
            f"if (({value} as string).length > {maximum}) fail({contract}, {path}, {reason});"
        )
    if schema.get("format") == "date-time":
        lines.append(
            f'if (Number.isNaN(Date.parse({value} as string))) fail({contract}, {path}, "expected date-time string");'
        )
    return lines


def _number_validation(
    schema: dict[str, Any], value: str, path: str, contract: str, integer: bool
) -> list[str]:
    lines = [
        f'if (typeof {value} !== "number" || !Number.isFinite({value})) fail({contract}, {path}, "expected finite number");'
    ]
    if integer:
        lines.append(
            f'if (!Number.isInteger({value})) fail({contract}, {path}, "expected integer");'
        )
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if isinstance(minimum, (int, float)):
        lines.append(
            f"if (({value} as number) < {minimum}) fail({contract}, {path}, {json.dumps(f'must be >= {minimum}')});"
        )
    if isinstance(maximum, (int, float)):
        lines.append(
            f"if (({value} as number) > {maximum}) fail({contract}, {path}, {json.dumps(f'must be <= {maximum}')});"
        )
    return lines


def _array_validation(
    schema: dict[str, Any], value: str, path: str, contract: str, stack: str
) -> list[str]:
    lines = [f'if (!Array.isArray({value})) fail({contract}, {path}, "expected array");']
    minimum, maximum = schema.get("minItems"), schema.get("maxItems")
    if isinstance(minimum, (int, float)):
        lines.append(
            f"if (({value} as unknown[]).length < {minimum}) fail({contract}, {path}, {json.dumps(f'must contain at least {minimum} items')});"
        )
    if isinstance(maximum, (int, float)):
        lines.append(
            f"if (({value} as unknown[]).length > {maximum}) fail({contract}, {path}, {json.dumps(f'must contain at most {maximum} items')});"
        )
    items = schema.get("items")
    if isinstance(items, dict):
        item_path = f"`${{{path}}}[${{index}}]`"
        rendered = _render_validation(
            items, value="item", path=item_path, contract=contract, stack=stack
        )
        if rendered:
            lines.append(f"({value} as unknown[]).forEach((item, index) => {{")
            lines.extend(_indent(rendered))
            lines.append("});")
    return lines


def _required_lines(schema: dict[str, Any], path: str, contract: str) -> list[str]:
    raw = schema.get("required", [])
    required = [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
    return [
        f'if (!Object.hasOwn(record, {json.dumps(key)})) fail({contract}, `${{{path}}}.{key}`, "missing required property");'
        for key in required
    ]


def _property_case(
    key: str, schema: dict[str, Any], path: str, contract: str, stack: str
) -> list[str]:
    rendered = _render_validation(
        schema,
        value="item",
        path=f"`${{{path}}}.{key}`",
        contract=contract,
        stack=stack,
    )
    if not rendered:
        return [f"    case {json.dumps(key)}: break;"]
    return [
        f"    case {json.dumps(key)}: {{",
        *_indent(rendered, 3),
        "      break;",
        "    }",
    ]


def _additional_lines(
    additional: Any, path: str, contract: str, stack: str, level: int
) -> list[str]:
    if isinstance(additional, dict):
        rendered = _render_validation(
            additional,
            value="item",
            path=f"`${{{path}}}.${{key}}`",
            contract=contract,
            stack=stack,
        )
        if not rendered:
            return ["  " * level + "break;"]
        return [*_indent(rendered, level), "  " * level + "break;"]
    if additional is True:
        return ["  " * level + "break;"]
    return ["  " * level + f'fail({contract}, `${{{path}}}.${{key}}`, "unexpected property");']


def _object_entries(schema: dict[str, Any], path: str, contract: str, stack: str) -> list[str]:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    body: list[str] = []
    property_names = schema.get("propertyNames")
    if isinstance(property_names, dict):
        body.extend(
            _render_validation(
                property_names,
                value="key",
                path=f"`${{{path}}}.${{key}}`",
                contract=contract,
                stack=stack,
            )
        )
    if properties:
        body.append("switch (key) {")
        for key, property_schema in properties.items():
            if isinstance(property_schema, dict):
                body.extend(_property_case(key, property_schema, path, contract, stack))
        body.append("  default: {")
        body.extend(_additional_lines(schema.get("additionalProperties"), path, contract, stack, 2))
        body.extend(["  }", "}"])
    else:
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            body.extend(
                _render_validation(
                    additional,
                    value="item",
                    path=f"`${{{path}}}.${{key}}`",
                    contract=contract,
                    stack=stack,
                )
            )
        elif additional is not True:
            body.append(f'fail({contract}, `${{{path}}}.${{key}}`, "unexpected property");')

    if not body:
        return []
    uses_key = _uses_identifier(body, "key")
    uses_item = _uses_identifier(body, "item")
    if uses_key and uses_item:
        header = "for (const [key, item] of Object.entries(record)) {"
    elif uses_key:
        header = "for (const key of Object.keys(record)) {"
    elif uses_item:
        header = "for (const item of Object.values(record)) {"
    else:
        return []
    return [header, *_indent(body), "}"]


def _object_validation(
    schema: dict[str, Any], value: str, path: str, contract: str, stack: str
) -> list[str]:
    lines = [f'if (!isRecord({value})) fail({contract}, {path}, "expected object");']
    record_checks = [
        *_required_lines(schema, path, contract),
        *_object_entries(schema, path, contract, stack),
    ]
    if record_checks:
        lines.append(f"const record = {value} as JsonRecord;")
        lines.extend(record_checks)
    return lines


def _render_validation(
    schema: dict[str, Any], *, value: str, path: str, contract: str, stack: str
) -> list[str]:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return _reference_validation(reference, value, path, contract, stack)
    options = schema.get("anyOf") if isinstance(schema.get("anyOf"), list) else schema.get("oneOf")
    if isinstance(options, list):
        return _union_validation(options, value, path, contract, stack)
    lines = _const_enum_validation(schema, value, path, contract)
    kind = schema.get("type")
    if kind == "null":
        return [
            *lines,
            f'if ({value} !== null) fail({contract}, {path}, "expected null");',
        ]
    if kind == "string":
        return [*lines, *_string_validation(schema, value, path, contract)]
    if kind in {"integer", "number"}:
        return [
            *lines,
            *_number_validation(schema, value, path, contract, kind == "integer"),
        ]
    if kind == "boolean":
        return [
            *lines,
            f'if (typeof {value} !== "boolean") fail({contract}, {path}, "expected boolean");',
        ]
    if kind == "array":
        return [*lines, *_array_validation(schema, value, path, contract, stack)]
    if kind == "object" or isinstance(schema.get("properties"), dict):
        return [*lines, *_object_validation(schema, value, path, contract, stack)]
    return lines


def _component_validator_lines(name: str, schema: dict[str, Any]) -> list[str]:
    rendered = _render_validation(
        schema, value="value", path="path", contract="contract", stack="refStack"
    )
    if not rendered:
        return [f"export function {_validator_name(name)}(): void {{", "}", ""]
    parameters = ["value: unknown", "path: string", "contract: string"]
    if _uses_identifier(rendered, "refStack"):
        parameters.append("refStack: Set<string>")
    lines = [f"export function {_validator_name(name)}({', '.join(parameters)}): void {{"]
    lines.extend(_indent(rendered))
    lines.extend(["}", ""])
    return lines


def _is_success_status(status: str) -> bool:
    try:
        code = int(status)
    except ValueError:
        return False
    return 200 <= code < 300


def _transport_error_schema_name(openapi: dict[str, Any]) -> str | None:
    names: set[str] = set()
    for path_item in openapi.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            for status, response in operation.get("responses", {}).items():
                if _is_success_status(str(status)) or not isinstance(response, dict):
                    continue
                schema = response.get("content", {}).get("application/json", {}).get("schema")
                if not isinstance(schema, dict):
                    continue
                reference = schema.get("$ref")
                if not isinstance(reference, str) or not reference.startswith(
                    "#/components/schemas/"
                ):
                    raise ValueError(
                        "Documented JSON error responses must reference a canonical component schema"
                    )
                names.add(ref_name(reference))
    if len(names) > 1:
        raise ValueError(
            f"Documented JSON error responses must use one transport envelope, found {sorted(names)}"
        )
    return next(iter(names), None)


def _transport_error_decoder_lines(schema_name: str | None) -> list[str]:
    if schema_name is None:
        return []
    validator = _validator_name(schema_name)
    schema_literal = json.dumps(schema_name)
    return [
        f'export function decodeTransportError(value: unknown): components["schemas"][{schema_literal}] {{',
        f'  {validator}(value, "$", "transport error", new Set());',
        f'  return value as components["schemas"][{schema_literal}];',
        "}",
        "",
    ]


def _operation_switch_lines(operations: tuple[ApiOperation, ...], direction: str) -> list[str]:
    is_request = direction == "Request"
    function_name = "decodeOperationRequest" if is_request else "decodeOperationResponse"
    noun = "request body" if is_request else "JSON response"
    lines = [
        f"export function {function_name}(name: OperationName, value: unknown): unknown {{",
        "  switch (name) {",
    ]
    for operation in operations:
        schema = operation.request_schema if is_request else operation.response_schema
        if schema is None or (not is_request and operation.response_kind != "json"):
            continue
        contract = json.dumps(f"{operation.operation_id} {direction.lower()}")
        rendered = _render_validation(
            schema, value="value", path='"$"', contract=contract, stack="new Set()"
        )
        lines.append(f"    case {json.dumps(operation.operation_id)}: {{")
        lines.extend(_indent(rendered, 3))
        lines.extend(["      return value;", "    }"])
    lines.extend(
        [
            f"    default: throw new Error(`Operation ${{name}} does not declare a {noun}`);",
            "  }",
            "}",
            "",
        ]
    )
    return lines


def _runtime_file_lines() -> list[str]:
    return """export type JsonRecord = Record<string, unknown>;
export type Validator = (value: unknown, path: string, contract: string, refStack: Set<string>) => void;

export class ContractValidationError extends Error {
  constructor(readonly contract: string, readonly path: string, readonly reason: string) {
    super(`Invalid ${contract} payload at ${path}: ${reason}`);
    this.name = "ContractValidationError";
  }
}

export function validateReference(name: string, validator: Validator, value: unknown, path: string, contract: string, refStack: Set<string>): void {
  if (refStack.has(name)) return;
  const nextStack = new Set(refStack);
  nextStack.add(name);
  validator(value, path, contract, nextStack);
}

export function validateUnion(contract: string, path: string, candidates: readonly (() => void)[]): void {
  for (const candidate of candidates) {
    try { candidate(); return; }
    catch (error) { if (!(error instanceof ContractValidationError)) throw error; }
  }
  fail(contract, path, "matched no allowed shape");
}

export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function fail(contract: string, path: string, reason: string): never {
  throw new ContractValidationError(contract, path, reason);
}
""".splitlines()


def _schema_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
            references.add(ref_name(reference))
        for child in value.values():
            references.update(_schema_references(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_schema_references(child))
    return references


def _validator_import_lines(
    names: Iterable[str], shard_by_name: dict[str, int], *, relative_prefix: str
) -> list[str]:
    grouped: dict[int, list[str]] = {}
    for name in sorted(set(names)):
        shard = shard_by_name.get(name)
        if shard is None:
            raise ValueError(f"Generated validator references unknown OpenAPI component {name!r}")
        grouped.setdefault(shard, []).append(_validator_name(name))
    return [
        f'import {{ {", ".join(grouped[index])} }} from "{relative_prefix}validatorPart{index:03d}";'
        for index in sorted(grouped)
    ]


def _render_validator_runtime_file(digest: str) -> str:
    return "\n".join(
        [
            f"// OPENAPI_SHA256: {digest}",
            "// AUTO-GENERATED shared runtime for static OpenAPI validators.",
            "// Do not edit this file by hand.",
            "",
            *_runtime_file_lines(),
            "",
        ]
    )


def _render_component_shards(
    schemas: dict[str, dict[str, Any]], digest: str
) -> tuple[dict[str, str], dict[str, int]]:
    shards = pack_blocks(
        (name, _component_validator_lines(name, schemas[name])) for name in sorted(schemas)
    )
    shard_by_name = {name: index for index, shard in enumerate(shards) for name, _lines in shard}
    outputs: dict[str, str] = {}
    for index, shard in enumerate(shards):
        own_names = {name for name, _lines in shard}
        referenced_names = (
            set().union(*(_schema_references(schemas[name]) for name in own_names)) - own_names
        )
        blocks = [line for _name, block in shard for line in block]
        runtime_import = _validator_runtime_import(blocks, "../validatorRuntime")
        imports = [
            *([runtime_import] if runtime_import is not None else []),
            *_validator_import_lines(referenced_names, shard_by_name, relative_prefix="./"),
        ]
        lines = [
            f"// OPENAPI_SHA256: {digest}",
            "// AUTO-GENERATED static OpenAPI component validator shard.",
            "// Do not edit this file by hand.",
            "",
            *imports,
            "",
            *blocks,
        ]
        outputs[f"validatorParts/validatorPart{index:03d}.ts"] = "\n".join(lines)
    return outputs, shard_by_name


def _operation_references(
    operations: tuple[ApiOperation, ...], error_schema_name: str | None
) -> set[str]:
    references: set[str] = set()
    for operation in operations:
        if operation.request_schema is not None:
            references.update(_schema_references(operation.request_schema))
        if operation.response_schema is not None:
            references.update(_schema_references(operation.response_schema))
    if error_schema_name is not None:
        references.add(error_schema_name)
    return references


def _render_validator_facade(
    operations: tuple[ApiOperation, ...],
    error_schema_name: str | None,
    shard_by_name: dict[str, int],
    digest: str,
) -> str:
    body = [
        *_transport_error_decoder_lines(error_schema_name),
        *_operation_switch_lines(operations, "Request"),
        *_operation_switch_lines(operations, "Response"),
    ]
    runtime_import = _validator_runtime_import(body, "@generated/validatorRuntime")
    root_lines = [
        f"// OPENAPI_SHA256: {digest}",
        "// AUTO-GENERATED static runtime validators compiled from FastAPI/OpenAPI.",
        "// Component validators live in deterministic internal shards; transport consumes this facade only.",
        "// This file executes schema-specific checks; it does not interpret JSON Schema at runtime.",
        "// Do not edit this file by hand.",
        "",
        *(
            ['import type { components } from "@generated/schema";']
            if _uses_identifier(body, "components")
            else []
        ),
        'import type { OperationName } from "@generated/operations";',
        *([runtime_import] if runtime_import is not None else []),
        *_validator_import_lines(
            _operation_references(operations, error_schema_name),
            shard_by_name,
            relative_prefix="@generated/validatorParts/",
        ),
        "",
        'export { ContractValidationError } from "@generated/validatorRuntime";',
        "",
        *body,
        "",
    ]
    return "\n".join(root_lines)


def render_validator_files(openapi: dict[str, Any]) -> dict[str, str]:
    schemas = component_schemas(openapi)
    operations = extract_operations(openapi)
    digest = openapi_digest(openapi)
    error_schema_name = _transport_error_schema_name(openapi)
    shard_outputs, shard_by_name = _render_component_shards(schemas, digest)
    return {
        "validatorRuntime.ts": _render_validator_runtime_file(digest),
        **shard_outputs,
        "validators.ts": _render_validator_facade(
            operations, error_schema_name, shard_by_name, digest
        ),
    }


def render_validators(openapi: dict[str, Any]) -> str:
    return render_validator_files(openapi)["validators.ts"]
