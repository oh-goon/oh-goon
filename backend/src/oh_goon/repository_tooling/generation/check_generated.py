from __future__ import annotations

import json
import sys
from pathlib import Path

from oh_goon.repository_tooling.generation.export_openapi import openapi_document, rendered_schema
from oh_goon.repository_tooling.generation.generate_api_types import render_generated_files
from oh_goon.repository_tooling.generation.goon_tool_authority import (
    OUTPUT as GOON_TOOL_AUTHORITY_PATH,
    render_goon_tool_authority,
)

ROOT = Path(__file__).resolve().parents[5]
OPENAPI_PATH = ROOT / "generated" / "openapi" / "openapi.json"
GENERATED_DIR = ROOT / "generated" / "frontend"


def _actual_frontend_files() -> dict[str, str]:
    if not GENERATED_DIR.exists():
        return {}
    return {
        path.relative_to(GENERATED_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(GENERATED_DIR.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    # Validate backend against the capture, then derive frontend expectations from
    # that exact capture so downstream facts are never built from a second walk.
    backend_document = openapi_document()
    expected_openapi = rendered_schema(backend_document)

    failures: list[str] = []
    actual_openapi = (
        OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.is_file() else None
    )
    if actual_openapi != expected_openapi:
        failures.append("generated/openapi/openapi.json")

    captured_document: dict[str, object] | None = None
    if actual_openapi is not None:
        try:
            loaded = json.loads(actual_openapi)
            if isinstance(loaded, dict):
                captured_document = loaded
        except json.JSONDecodeError:
            pass
    expected_frontend = (
        render_generated_files(captured_document)
        if captured_document is not None
        else {}
    )
    actual_frontend = _actual_frontend_files()
    for relative in sorted(set(expected_frontend) | set(actual_frontend)):
        if actual_frontend.get(relative) != expected_frontend.get(relative):
            failures.append(f"generated/frontend/{relative}")

    expected_goon_tool_authority = render_goon_tool_authority(ROOT)
    actual_goon_tool_authority = (
        GOON_TOOL_AUTHORITY_PATH.read_text(encoding="utf-8")
        if GOON_TOOL_AUTHORITY_PATH.is_file()
        else None
    )
    if actual_goon_tool_authority != expected_goon_tool_authority:
        failures.append(str(GOON_TOOL_AUTHORITY_PATH.relative_to(ROOT)))

    if failures:
        print("Generated contracts are stale or missing:", file=sys.stderr)
        for path in failures:
            print(f"  - {path}", file=sys.stderr)
        print(
            "Run `make generate` and commit the regenerated contract artifacts.",
            file=sys.stderr,
        )
        return 1
    print("Generated contracts are current and were checked without rewriting them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
