from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling.generation.api_operation_generation import (
    ApiOperation,
    ApiParameter,
    extract_operations,
    render_operation_files,
    render_operations,
)
from oh_goon.repository_tooling.generation.api_schema_generation import (
    component_schemas,
    render,
    render_enums,
    render_models,
    render_schema_files,
    runtime_enums,
    ts_type,
)
from oh_goon.repository_tooling.generation.api_validator_generation import (
    render_validator_files,
    render_validators,
)
from oh_goon.repository_tooling.generation.generated_typescript import assert_typescript_file_limit

ROOT = Path(__file__).resolve().parents[5]
OPENAPI_PATH = ROOT / "generated" / "openapi" / "openapi.json"
GENERATED_DIR = ROOT / "generated" / "frontend"

__all__ = [
    "ApiOperation",
    "ApiParameter",
    "component_schemas",
    "runtime_enums",
    "extract_operations",
    "render",
    "render_enums",
    "render_models",
    "render_operation_files",
    "render_operations",
    "render_schema_files",
    "render_validator_files",
    "render_validators",
    "render_generated_files",
    "ts_type",
]


def render_generated_files(openapi: dict[str, Any]) -> dict[str, str]:
    outputs = {
        **render_schema_files(openapi),
        "enums.ts": render_enums(openapi),
        "models.ts": render_models(openapi),
        **render_operation_files(openapi),
        **render_validator_files(openapi),
    }
    assert_typescript_file_limit(outputs)
    return dict(sorted(outputs.items()))


def load_captured_openapi(path: Path = OPENAPI_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(
            "generated/openapi/openapi.json is missing; run `make api-schema` first"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(
            "generated/openapi/openapi.json must contain one OpenAPI object"
        )
    return payload


def _remove_stale_generated_files(expected: set[str], *, generated_dir: Path = GENERATED_DIR) -> None:
    if not generated_dir.exists():
        return
    for path in sorted(generated_dir.rglob("*"), reverse=True):
        if (
            path.is_file()
            and path.relative_to(generated_dir).as_posix() not in expected
        ):
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def write_generated_api_types(
    openapi: dict[str, Any], *, generated_dir: Path = GENERATED_DIR
) -> tuple[Path, ...]:
    outputs = render_generated_files(openapi)
    generated_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_generated_files(set(outputs), generated_dir=generated_dir)
    written: list[Path] = []
    for relative_path, content in outputs.items():
        path = generated_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return tuple(written)


def main() -> int:
    written = write_generated_api_types(load_captured_openapi())
    for path in written:
        print(f"Generated {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
