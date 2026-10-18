from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from oh_goon.repository_tooling.generation.generate_api_types import (
    component_schemas,
    extract_operations,
    render_models,
    render_generated_files,
    render_operations,
    render_validator_files,
    render_validators,
    runtime_enums,
    ts_type,
)


def test_array_of_union_is_parenthesized() -> None:
    schema = {
        "type": "array",
        "items": {
            "oneOf": [
                {"$ref": "#/components/schemas/RunStartedEvent"},
                {"$ref": "#/components/schemas/RunFailedEvent"},
            ]
        },
    }

    assert ts_type(schema) == (
        '(components["schemas"]["RunStartedEvent"] | '
        'components["schemas"]["RunFailedEvent"])[]'
    )


def test_array_of_enum_union_is_parenthesized() -> None:
    schema = {"type": "array", "items": {"enum": ["one", "two"]}}

    assert ts_type(schema) == '("one" | "two")[]'


def test_runtime_constants_are_derived_from_named_openapi_unions_without_creating_schemas() -> (
    None
):
    openapi = {
        "components": {
            "schemas": {
                "FleetRunState": {
                    "discriminator": {
                        "propertyName": "status",
                        "mapping": {
                            "QUEUED": "#/components/schemas/QueuedFleetRun",
                            "RUNNING": "#/components/schemas/RunningFleetRun",
                        },
                    },
                    "oneOf": [
                        {"$ref": "#/components/schemas/QueuedFleetRun"},
                        {"$ref": "#/components/schemas/RunningFleetRun"},
                    ],
                },
                "OperationTarget": {
                    "discriminator": {
                        "propertyName": "kind",
                        "mapping": {
                            "dependency": "#/components/schemas/DependencyTarget",
                            "fleet_plan": "#/components/schemas/FleetPlanTarget",
                        },
                    },
                    "oneOf": [
                        {"$ref": "#/components/schemas/DependencyTarget"},
                        {"$ref": "#/components/schemas/FleetPlanTarget"},
                    ],
                },
            }
        }
    }

    assert runtime_enums(openapi)["FleetRunStatus"] == ("QUEUED", "RUNNING")
    assert runtime_enums(openapi)["OperationTargetKind"] == ("dependency", "fleet_plan")
    assert set(component_schemas(openapi)) == {"FleetRunState", "OperationTarget"}


def test_anonymous_discriminated_union_is_not_promoted_to_a_component_schema() -> None:
    openapi = {
        "paths": {
            "/api/report": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "discriminator": {
                                        "propertyName": "outcome",
                                        "mapping": {
                                            "committed": "#/components/schemas/CommittedReportExportRequest",
                                            "failed": "#/components/schemas/FailedReportExportRequest",
                                        },
                                    },
                                    "oneOf": [
                                        {
                                            "$ref": "#/components/schemas/CommittedReportExportRequest"
                                        },
                                        {
                                            "$ref": "#/components/schemas/FailedReportExportRequest"
                                        },
                                    ],
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "CommittedReportExportRequest": {"type": "object"},
                "FailedReportExportRequest": {"type": "object"},
            }
        },
    }

    assert set(component_schemas(openapi)) == {
        "CommittedReportExportRequest",
        "FailedReportExportRequest",
    }
    assert "ReportExportRequest" not in component_schemas(openapi)
    assert "ReportExportRequestOutcome" not in runtime_enums(openapi)


def test_generated_model_aliases_reference_generated_schema_once() -> None:
    rendered = render_models(
        {"components": {"schemas": {"Run": {"type": "object", "properties": {}}}}}
    )

    assert 'import type { components } from "./schema";' in rendered
    assert 'export type Run = components["schemas"]["Run"];' in rendered


def test_operation_contract_is_derived_from_openapi_facts() -> None:
    openapi = {
        "paths": {
            "/api/runs/{run_id}": {
                "get": {
                    "operationId": "get_run",
                    "parameters": [
                        {
                            "name": "run_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "include_events",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Run"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {"schemas": {"Run": {"type": "object", "properties": {}}}},
    }

    operation = extract_operations(openapi)[0]

    assert operation.operation_id == "get_run"
    assert operation.method == "GET"
    assert operation.frontend_path == "/runs/{run_id}"
    assert [parameter.name for parameter in operation.path_parameters] == ["run_id"]
    assert [parameter.name for parameter in operation.query_parameters] == [
        "include_events"
    ]
    assert operation.request_schema is None
    assert operation.response_schema == {"$ref": "#/components/schemas/Run"}


def test_operation_generation_fails_without_stable_operation_id() -> None:
    openapi = {
        "paths": {
            "/api/runs": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {}}
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    with pytest.raises(ValueError, match="missing stable operationId"):
        extract_operations(openapi)


def test_operation_generation_fails_on_ambiguous_success_contract() -> None:
    openapi = {
        "paths": {
            "/api/runs": {
                "post": {
                    "operationId": "start_run",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            }
                        },
                        "201": {
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            }
                        },
                    },
                }
            }
        }
    }

    with pytest.raises(ValueError, match="exactly one explicit 2xx response"):
        extract_operations(openapi)


def test_rendered_operation_contract_contains_transport_facts_not_policy() -> None:
    openapi = {
        "paths": {
            "/api/runs": {
                "post": {
                    "operationId": "start_run",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RunRequest"}
                            }
                        },
                    },
                    "responses": {
                        "202": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Run"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "RunRequest": {"type": "object", "properties": {}},
                "Run": {"type": "object", "properties": {}},
            }
        },
    }

    rendered = render_operations(openapi)
    validators = render_validators(openapi)

    assert 'method: "POST"' in rendered
    assert 'path: "/runs"' in rendered
    assert 'requestSchema: "RunRequest"' in rendered
    assert 'responseSchema: "Run"' in rendered
    assert "export function decodeOperationRequest" in validators
    assert "export function decodeOperationResponse" in validators
    assert 'case "start_run": {' in validators
    assert "decodeOperationStartRun" not in validators
    assert "cache" not in rendered.lower()
    assert "MutationController" not in rendered


def test_public_api_routes_declare_explicit_semantic_operation_ids() -> None:
    from oh_goon.api.app import create_app

    app = create_app()
    route_root = Path(__file__).resolve().parents[2] / "backend/src/oh_goon/api/routes"
    ids: list[str] = []
    for path in route_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                owner = decorator.func.value
                if not isinstance(owner, ast.Name) or owner.id != "router":
                    continue
                if decorator.func.attr not in {"delete", "get", "patch", "post", "put"}:
                    continue
                values = [
                    item.value
                    for item in decorator.keywords
                    if item.arg == "operation_id"
                ]
                assert len(values) == 1
                assert isinstance(values[0], ast.Constant) and isinstance(
                    values[0].value, str
                )
                ids.append(values[0].value)

    assert ids
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", operation_id) for operation_id in ids)
    assert all("_api_" not in operation_id for operation_id in ids)

    openapi_ids = {
        operation["operationId"]
        for path_item in app.openapi()["paths"].values()
        for method, operation in path_item.items()
        if method in {"delete", "get", "patch", "post", "put"}
    }
    assert openapi_ids == set(ids)


def test_validator_generation_keeps_component_decoders_private_and_derives_transport_error_entry_point() -> (
    None
):
    openapi = {
        "paths": {
            "/api/runs": {
                "post": {
                    "operationId": "start_run",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Run"}
                                }
                            }
                        },
                        "400": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiErrorResponse"
                                    }
                                }
                            }
                        },
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Run": {"type": "object", "properties": {}},
                "ApiErrorResponse": {"type": "object", "properties": {}},
            }
        },
    }

    validators = render_validators(openapi)
    exported_decoders = set(
        re.findall(r"^export function (decode[A-Za-z0-9_]+)", validators, re.MULTILINE)
    )

    assert exported_decoders == {
        "decodeOperationRequest",
        "decodeOperationResponse",
        "decodeTransportError",
    }
    assert 'components["schemas"]["ApiErrorResponse"]' in validators
    assert "export function decodeApiErrorResponse" not in validators
    assert "export function decodeRun" not in validators


def test_validator_generation_rejects_ambiguous_transport_error_envelopes() -> None:
    openapi = {
        "paths": {
            "/api/runs": {
                "get": {
                    "operationId": "get_run",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            }
                        },
                        "400": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiErrorResponse"
                                    }
                                }
                            }
                        },
                        "409": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ConflictResponse"
                                    }
                                }
                            }
                        },
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "ApiErrorResponse": {"type": "object"},
                "ConflictResponse": {"type": "object"},
            }
        },
    }

    with pytest.raises(ValueError, match="one transport envelope"):
        render_validators(openapi)


def test_validator_generation_omits_noop_walks_and_unused_reference_stack() -> None:
    openapi = {
        "paths": {
            "/api/container": {
                "get": {
                    "operationId": "get_container",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Container"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Container": {
                    "type": "object",
                    "properties": {
                        "details": {"type": "object", "additionalProperties": True},
                        "values": {"type": "array", "items": {}},
                    },
                    "additionalProperties": False,
                },
                "ReferencedContainer": {
                    "type": "object",
                    "properties": {
                        "container": {"$ref": "#/components/schemas/Container"}
                    },
                    "additionalProperties": False,
                },
            }
        },
    }

    validators = "\n".join(render_validator_files(openapi).values())

    assert (
        "function validateContainer(value: unknown, path: string, contract: string): void {"
        in validators
    )
    assert (
        "function validateReferencedContainer(value: unknown, path: string, contract: string, refStack: Set<string>): void {"
        in validators
    )
    assert not re.search(r"for \([^\n]+\) \{\n\s*\}", validators)
    assert not re.search(r"\.forEach\([^\n]+=> \{\n\s*\}\);", validators)


def test_generated_typescript_is_sharded_below_the_repository_line_limit() -> None:
    from oh_goon.api.app import create_app
    from oh_goon.repository_tooling.generation.generated_typescript import MAX_TYPESCRIPT_LINES

    openapi = create_app().openapi()
    outputs = render_generated_files(openapi)
    line_counts = {
        name: len(content.splitlines())
        for name, content in outputs.items()
        if name.endswith((".ts", ".tsx", ".mts", ".cts"))
    }

    assert max(line_counts.values()) <= MAX_TYPESCRIPT_LINES
    assert line_counts["validators.ts"] <= MAX_TYPESCRIPT_LINES
    assert line_counts["operations.ts"] <= MAX_TYPESCRIPT_LINES
    assert line_counts["operationTypes.ts"] <= MAX_TYPESCRIPT_LINES
    assert any(name.startswith("validatorParts/") for name in outputs)
    assert any(name.startswith("schemaParts/") for name in outputs)


def test_openapi_generation_reads_current_checkout_version_on_each_capture(
    tmp_path: Path, monkeypatch
) -> None:
    from oh_goon.repository_tooling.generation import export_openapi

    version = tmp_path / "VERSION"
    version.write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(export_openapi, "ROOT", tmp_path)

    first = export_openapi.openapi_document()
    version.write_text("1.2.4\n", encoding="utf-8")
    second = export_openapi.openapi_document()

    assert first["info"]["version"] == "1.2.3"
    assert second["info"]["version"] == "1.2.4"


def test_validator_facade_imports_only_runtime_helpers_used_by_operation_shapes() -> (
    None
):
    openapi = {
        "paths": {
            "/api/container": {
                "get": {
                    "operationId": "get_container",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Container"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Container": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                }
            }
        },
    }

    facade = render_validators(openapi)

    assert 'import { validateReference } from "@generated/validatorRuntime";' in facade
    assert "isRecord" not in facade
    assert "JsonRecord" not in facade


def test_validator_facade_imports_object_helpers_for_inline_operation_shape() -> None:
    openapi = {
        "paths": {
            "/api/container": {
                "get": {
                    "operationId": "get_container",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                        "required": ["name"],
                                        "additionalProperties": False,
                                    }
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {"schemas": {}},
    }

    facade = render_validators(openapi)

    assert (
        'import { fail, isRecord, type JsonRecord } from "@generated/validatorRuntime";'
        in facade
    )
