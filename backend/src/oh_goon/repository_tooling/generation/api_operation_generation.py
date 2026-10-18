from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from oh_goon.repository_tooling.generation.api_schema_generation import openapi_digest, ref_name, ts_type
from oh_goon.repository_tooling.generation.generated_typescript import pack_blocks

HTTP_METHODS = ("delete", "get", "patch", "post", "put")
API_PREFIX = "/api"


@dataclass(frozen=True)
class ApiParameter:
    name: str
    location: str
    required: bool
    schema: dict[str, Any]


@dataclass(frozen=True)
class ApiOperation:
    operation_id: str
    method: str
    path: str
    parameters: tuple[ApiParameter, ...]
    request_schema: dict[str, Any] | None
    request_required: bool
    response_schema: dict[str, Any] | None
    success_status: int
    response_kind: str

    @property
    def frontend_path(self) -> str:
        if not self.path.startswith(API_PREFIX):
            raise ValueError(
                f"Frontend operation path must start with {API_PREFIX!r}: {self.path}"
            )
        return self.path[len(API_PREFIX) :] or "/"

    @property
    def path_parameters(self) -> tuple[ApiParameter, ...]:
        return tuple(
            parameter for parameter in self.parameters if parameter.location == "path"
        )

    @property
    def query_parameters(self) -> tuple[ApiParameter, ...]:
        return tuple(
            parameter for parameter in self.parameters if parameter.location == "query"
        )


def _json_schema(content: Any, *, owner: str) -> dict[str, Any] | None:
    if content is None:
        return None
    if not isinstance(content, dict):
        raise ValueError(f"{owner} content must be an object")
    media = content.get("application/json")
    if media is None:
        return None
    if not isinstance(media, dict) or not isinstance(media.get("schema"), dict):
        raise ValueError(f"{owner} application/json must declare a schema")
    return media["schema"]


def _extract_parameters(
    operation_id: str, raw_parameters: list[Any]
) -> tuple[ApiParameter, ...]:
    parameters: list[ApiParameter] = []
    seen: set[tuple[str, str]] = set()
    for parameter in raw_parameters:
        if not isinstance(parameter, dict):
            raise ValueError(f"Parameter must be an object for {operation_id}")
        name, location, schema = (
            parameter.get("name"),
            parameter.get("in"),
            parameter.get("schema"),
        )
        if not isinstance(name, str) or not name:
            raise ValueError(f"Parameter name missing for {operation_id}")
        if location not in {"path", "query"}:
            raise ValueError(
                f"Unsupported parameter location {location!r} for {operation_id}.{name}"
            )
        if not isinstance(schema, dict):
            raise ValueError(f"Parameter schema missing for {operation_id}.{name}")
        key = (location, name)
        if key in seen:
            raise ValueError(
                f"Duplicate parameter {location}.{name} for {operation_id}"
            )
        seen.add(key)
        required = bool(parameter.get("required", False))
        if location == "path" and not required:
            raise ValueError(f"Path parameter must be required: {operation_id}.{name}")
        parameters.append(ApiParameter(name, location, required, schema))
    return tuple(parameters)


def _extract_request(
    operation_id: str, raw: dict[str, Any]
) -> tuple[dict[str, Any] | None, bool]:
    request_body = raw.get("requestBody")
    if request_body is None:
        return None, False
    if not isinstance(request_body, dict):
        raise ValueError(f"requestBody must be an object for {operation_id}")
    schema = _json_schema(request_body.get("content"), owner=f"{operation_id} request")
    if schema is None:
        raise ValueError(
            f"Only application/json request bodies are supported: {operation_id}"
        )
    return schema, bool(request_body.get("required", False))


def _extract_success(
    operation_id: str, raw: dict[str, Any]
) -> tuple[int, dict[str, Any] | None, str]:
    responses = raw.get("responses")
    if not isinstance(responses, dict):
        raise ValueError(f"Responses missing for {operation_id}")
    successful: list[tuple[int, dict[str, Any]]] = []
    for code, response in responses.items():
        try:
            status = int(code)
        except (TypeError, ValueError):
            continue
        if 200 <= status < 300:
            if not isinstance(response, dict):
                raise ValueError(
                    f"Response {code} must be an object for {operation_id}"
                )
            successful.append((status, response))
    if len(successful) != 1:
        raise ValueError(
            f"Operation {operation_id} must have exactly one explicit 2xx response; found {len(successful)}"
        )
    status, response = successful[0]
    schema = _json_schema(response.get("content"), owner=f"{operation_id} response")
    if status == 204:
        if schema is not None:
            raise ValueError(
                f"204 response must not declare JSON schema: {operation_id}"
            )
        return status, None, "void"
    return status, schema, "json" if schema is not None else "raw"


def _extract_operation(
    path: str, method: str, raw: dict[str, Any], inherited: list[Any]
) -> ApiOperation:
    operation_id = raw.get("operationId")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ValueError(
            f"Operation is missing stable operationId: {method.upper()} {path}"
        )
    own_parameters = raw.get("parameters", [])
    if not isinstance(own_parameters, list):
        raise ValueError(f"Operation parameters must be a list for {operation_id}")
    parameters = _extract_parameters(operation_id, [*inherited, *own_parameters])
    request_schema, request_required = _extract_request(operation_id, raw)
    success_status, response_schema, response_kind = _extract_success(operation_id, raw)
    operation = ApiOperation(
        operation_id,
        method.upper(),
        path,
        parameters,
        request_schema,
        request_required,
        response_schema,
        success_status,
        response_kind,
    )
    _ = operation.frontend_path
    return operation


def extract_operations(openapi: dict[str, Any]) -> tuple[ApiOperation, ...]:
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI paths must be an object")
    operations: list[ApiOperation] = []
    seen_ids: set[str] = set()
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, dict):
            raise ValueError(f"OpenAPI path item must be an object: {path}")
        inherited = path_item.get("parameters", [])
        if not isinstance(inherited, list):
            raise ValueError(f"Path-level parameters must be a list: {path}")
        for method in HTTP_METHODS:
            raw = path_item.get(method)
            if raw is None:
                continue
            if not isinstance(raw, dict):
                raise ValueError(
                    f"Operation must be an object: {method.upper()} {path}"
                )
            operation = _extract_operation(path, method, raw, inherited)
            if operation.operation_id in seen_ids:
                raise ValueError(
                    f"Duplicate OpenAPI operationId: {operation.operation_id}"
                )
            seen_ids.add(operation.operation_id)
            operations.append(operation)
    return tuple(sorted(operations, key=lambda operation: operation.operation_id))


def _parameter_object_type(parameters: Iterable[ApiParameter]) -> str:
    lines = ["{"]
    for parameter in parameters:
        optional = "" if parameter.required else "?"
        lines.append(
            f"      readonly {json.dumps(parameter.name)}{optional}: {ts_type(parameter.schema)};"
        )
    lines.append("    }")
    return "\n".join(lines)


def _operation_call_type(operation: ApiOperation) -> str:
    fields: list[tuple[str, str, bool]] = []
    if operation.path_parameters:
        fields.append(("path", _parameter_object_type(operation.path_parameters), True))
    if operation.query_parameters:
        fields.append(
            (
                "query",
                _parameter_object_type(operation.query_parameters),
                any(p.required for p in operation.query_parameters),
            )
        )
    if operation.request_schema is not None:
        fields.append(
            ("body", ts_type(operation.request_schema), operation.request_required)
        )
    if not fields:
        return (
            "{ readonly path?: never; readonly query?: never; readonly body?: never }"
        )
    lines = ["{"]
    for name, rendered, required in fields:
        optional = "" if required else "?"
        rendered_lines = rendered.splitlines()
        if len(rendered_lines) == 1:
            lines.append(f"    readonly {name}{optional}: {rendered_lines[0]};")
        else:
            lines.append(f"    readonly {name}{optional}: {rendered_lines[0]}")
            lines.extend(rendered_lines[1:-1])
            lines.append(f"{rendered_lines[-1]};")
    if not operation.path_parameters:
        lines.append("    readonly path?: never;")
    if not operation.query_parameters:
        lines.append("    readonly query?: never;")
    if operation.request_schema is None:
        lines.append("    readonly body?: never;")
    lines.append("  }")
    return "\n".join(lines)


def _schema_fact(schema: dict[str, Any] | None) -> str | None:
    if schema is None:
        return None
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        return ref_name(reference)
    return "<inline>"


def _metadata_lines(operation: ApiOperation) -> list[str]:
    path_parameters = json.dumps(
        [parameter.name for parameter in operation.path_parameters]
    )
    query_parameters = json.dumps(
        [parameter.name for parameter in operation.query_parameters]
    )
    required_query_parameters = json.dumps(
        [
            parameter.name
            for parameter in operation.query_parameters
            if parameter.required
        ]
    )
    request_schema = (
        json.dumps(_schema_fact(operation.request_schema))
        if operation.request_schema is not None
        else "null"
    )
    response_schema = (
        json.dumps(_schema_fact(operation.response_schema))
        if operation.response_schema is not None
        else "null"
    )
    return [
        f"  {operation.operation_id}: {{",
        f"    operationId: {json.dumps(operation.operation_id)}, method: {json.dumps(operation.method)}, path: {json.dumps(operation.frontend_path)}, successStatus: {operation.success_status},",
        f"    pathParameters: {path_parameters}, queryParameters: {query_parameters}, requiredQueryParameters: {required_query_parameters},",
        f"    requestSchema: {request_schema}, requestRequired: {str(operation.request_required).lower()}, responseSchema: {response_schema}, responseKind: {json.dumps(operation.response_kind)},",
        "  },",
    ]


def _type_lines(operation: ApiOperation) -> list[str]:
    response = (
        "void"
        if operation.response_kind == "void"
        else "unknown"
        if operation.response_kind == "raw"
        else ts_type(operation.response_schema or {})
    )
    call_lines = _operation_call_type(operation).splitlines()
    lines = [f"  {operation.operation_id}: {{"]
    if len(call_lines) == 1:
        lines.append(f"    call: {call_lines[0]};")
    else:
        lines.append(f"    call: {call_lines[0]}")
        lines.extend(call_lines[1:-1])
        lines.append(f"{call_lines[-1]};")
    lines.extend([f"    response: {response};", "  };"])
    return lines


def _runtime_lines() -> list[str]:
    return """export type OperationCall<Name extends OperationName> = operationTypes[Name]["call"];
export type OperationResponse<Name extends OperationName> = operationTypes[Name]["response"];

type RuntimeCall = { readonly path?: Record<string, unknown>; readonly query?: Record<string, unknown>; readonly body?: unknown };

export function operationUrl<Name extends OperationName>(name: Name, call: OperationCall<Name>): string {
  const metadata = operations[name];
  const runtimeCall = call as RuntimeCall;
  let url: string = metadata.path;
  for (const parameter of metadata.pathParameters) {
    const value = runtimeCall.path?.[parameter];
    if (value === undefined || value === null) throw new Error(`Missing path parameter ${parameter} for ${name}`);
    url = url.replace(`{${parameter}}`, encodeURIComponent(String(value)));
  }
  if (url.includes("{")) throw new Error(`Unresolved path parameter for ${name}: ${url}`);
  const query = new URLSearchParams();
  for (const parameter of metadata.requiredQueryParameters) {
    const value = runtimeCall.query?.[parameter];
    if (value === undefined || value === null) throw new Error(`Missing query parameter ${parameter} for ${name}`);
  }
  for (const parameter of metadata.queryParameters) appendQuery(query, parameter, runtimeCall.query?.[parameter]);
  const encoded = query.toString();
  return encoded === "" ? url : `${url}?${encoded}`;
}

export function operationBody<Name extends OperationName>(name: Name, call: OperationCall<Name>): string | undefined {
  const metadata = operations[name];
  const body = (call as RuntimeCall).body;
  if (metadata.requestSchema === null) {
    if (body !== undefined) throw new Error(`Operation ${name} does not accept a request body`);
    return undefined;
  }
  if (body === undefined) {
    if (!metadata.requestRequired) return undefined;
    throw new Error(`Operation ${name} requires a request body`);
  }
  decodeOperationRequest(name, body);
  return JSON.stringify(body);
}

export function decodeResponse<Name extends OperationName>(name: Name, value: unknown): OperationResponse<Name> {
  return decodeOperationResponse(name, value) as OperationResponse<Name>;
}

function appendQuery(query: URLSearchParams, name: string, value: unknown): void {
  if (value === undefined || value === null) return;
  if (Array.isArray(value)) { value.forEach((item) => appendQuery(query, name, item)); return; }
  query.append(name, String(value));
}
""".splitlines()


def render_operation_files(openapi: dict[str, Any]) -> dict[str, str]:
    operations = extract_operations(openapi)
    digest = openapi_digest(openapi)
    type_shards = pack_blocks(
        (operation.operation_id, _type_lines(operation)) for operation in operations
    )
    type_outputs: dict[str, str] = {}
    type_part_names: list[str] = []
    for index, shard in enumerate(type_shards):
        part_name = f"OperationTypePart{index:03d}"
        type_part_names.append(part_name)
        lines = [
            f"// OPENAPI_SHA256: {digest}",
            "// AUTO-GENERATED operation call/response type shard.",
            "// Do not edit this file by hand.",
            'import type { components } from "@generated/schema";',
            "",
            f"export interface {part_name} {{",
        ]
        for _name, block in shard:
            lines.extend(block)
        lines.extend(["}", ""])
        type_outputs[f"operationTypeParts/operationTypePart{index:03d}.ts"] = "\n".join(lines)

    type_lines = [
        f"// OPENAPI_SHA256: {digest}",
        "// AUTO-GENERATED operation call/response types from deterministic shards.",
        "// Do not edit this file by hand.",
        "",
    ]
    for index, part_name in enumerate(type_part_names):
        type_lines.append(
            f'import type {{ {part_name} }} from "./operationTypeParts/operationTypePart{index:03d}";'
        )
    type_lines.extend(["", f"export interface operationTypes extends {', '.join(type_part_names)} {{}}", ""])

    root_lines = [
        f"// OPENAPI_SHA256: {digest}",
        "// AUTO-GENERATED operation facts from FastAPI/OpenAPI.",
        "// Contains transport facts only; frontend behavior stays handwritten.",
        "// Do not edit this file by hand.",
        "",
        'import type { operationTypes } from "@generated/operationTypes";',
        'import { decodeOperationRequest, decodeOperationResponse } from "@generated/validators";',
        "",
        'export type HttpMethod = "DELETE" | "GET" | "PATCH" | "POST" | "PUT";',
        'export type ResponseKind = "json" | "raw" | "void";',
        "",
        "export interface OperationMetadata {",
        "  readonly operationId: string;",
        "  readonly method: HttpMethod;",
        "  readonly path: string;",
        "  readonly pathParameters: readonly string[];",
        "  readonly queryParameters: readonly string[];",
        "  readonly requiredQueryParameters: readonly string[];",
        "  readonly requestSchema: string | null;",
        "  readonly requestRequired: boolean;",
        "  readonly responseSchema: string | null;",
        "  readonly responseKind: ResponseKind;",
        "  readonly successStatus: number;",
        "}",
        "",
        "export const operations = {",
    ]
    for operation in operations:
        root_lines.extend(_metadata_lines(operation))
    root_lines.extend(
        [
            "} as const satisfies Record<string, OperationMetadata>;",
            "",
            "export type OperationName = keyof typeof operations;",
            "",
            *_runtime_lines(),
            "",
        ]
    )
    return {
        **type_outputs,
        "operationTypes.ts": "\n".join(type_lines),
        "operations.ts": "\n".join(root_lines),
    }


def render_operations(openapi: dict[str, Any]) -> str:
    return render_operation_files(openapi)["operations.ts"]
