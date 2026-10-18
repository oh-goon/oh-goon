from __future__ import annotations

import json
from pathlib import Path

from oh_goon.api.app import create_app
from oh_goon.repository_tooling.generation.export_openapi import rendered_schema
from oh_goon.repository_tooling.generation.generate_api_types import render_generated_files

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
