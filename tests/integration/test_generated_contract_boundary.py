from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from oh_goon.api.app import create_app
from oh_goon.repository_tooling.generation.export_openapi import rendered_schema
from oh_goon.repository_tooling.generation.generate_api_types import (
    render_generated_files,
)

ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "generated" / "openapi" / "openapi.json"
FRONTEND_CONTRACT_ROOT = ROOT / "generated" / "frontend"


def test_backend_openapi_and_generated_frontend_share_one_captured_contract() -> None:
    captured_text = OPENAPI_PATH.read_text(encoding="utf-8")
    assert captured_text == rendered_schema(create_app().openapi())

    captured = json.loads(captured_text)
    expected = render_generated_files(captured)
    actual = {
        path.relative_to(FRONTEND_CONTRACT_ROOT).as_posix(): path.read_text(
            encoding="utf-8"
        )
        for path in FRONTEND_CONTRACT_ROOT.rglob("*")
        if path.is_file()
    }
    assert actual == expected


@pytest.mark.parametrize("property_count", [8, 900])
def test_generated_contract_preserves_wide_models_at_native_typescript_boundary(
    tmp_path: Path, property_count: int
) -> None:
    from fastapi import FastAPI
    from pydantic import create_model

    fields: dict[str, Any] = {
        f"field_{index}": (str, ...) for index in range(property_count)
    }
    model = create_model("WideResponse", **fields)
    app = FastAPI()

    @app.get("/api/wide", response_model=model, operation_id="read_wide")
    def wide() -> dict[str, str]:
        return {name: "value" for name in fields}

    outputs = render_generated_files(app.openapi())
    generated = tmp_path / "generated"
    for relative, content in outputs.items():
        path = generated / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    last = f"field_{property_count - 1}"
    payload = json.dumps({name: "value" for name in fields})
    (tmp_path / "consumer.ts").write_text(
        'import { decodeResponse } from "@generated/operations";\n'
        'import type { OperationResponse } from "@generated/operations";\n'
        f'const valid: OperationResponse<"read_wide"> = {payload};\n'
        f'if (decodeResponse("read_wide", valid).{last} !== "value") '
        'throw new Error("Last field was lost");\n'
        "// @ts-expect-error The final property must retain its generated type.\n"
        f'const invalid: OperationResponse<"read_wide"> = {{ ...valid, {last}: 42 }};\n'
        "let rejected = false;\n"
        'try { decodeResponse("read_wide", invalid); } catch { rejected = true; }\n'
        'if (!rejected) throw new Error("Invalid final field was accepted");\n',
        encoding="utf-8",
    )
    config = tmp_path / "tsconfig.json"
    config.write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "ESNext",
                    "moduleResolution": "Bundler",
                    "strict": True,
                    "noEmit": True,
                    "skipLibCheck": True,
                    "types": [],
                    "paths": {"@generated/*": ["./generated/*"]},
                },
                "include": ["consumer.ts", "generated/**/*.ts"],
            }
        ),
        encoding="utf-8",
    )
    bundler_config = tmp_path / "rolldown.config.mjs"
    bundler_config.write_text(
        "export default "
        + json.dumps(
            {
                "input": str(tmp_path / "consumer.ts"),
                "tsconfig": str(config),
                "platform": "node",
                "output": {"file": str(tmp_path / "consumer.cjs"), "format": "cjs"},
            }
        )
        + ";\n",
        encoding="utf-8",
    )
    tools = ROOT / "frontend/node_modules"
    for command in (
        ["node", str(tools / "typescript/bin/tsc"), "--project", str(config)],
        ["node", str(tools / "rolldown/bin/cli.mjs"), "--config", str(bundler_config)],
        ["node", str(tmp_path / "consumer.cjs")],
    ):
        result = subprocess.run(
            command, cwd=tmp_path, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stdout + result.stderr
