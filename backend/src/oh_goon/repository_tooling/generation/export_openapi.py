from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from oh_goon.api.app import create_app

ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUTPUT = ROOT / "generated" / "openapi" / "openapi.json"


def _source_product_version() -> str:
    """Read current checkout identity at generation time, not import time."""

    path = ROOT / "VERSION"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("VERSION must be a regular non-symlink file for OpenAPI generation")
    value = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise RuntimeError(f"VERSION must contain canonical x.y.z identity, got {value!r}")
    return value


def openapi_document() -> dict[str, object]:
    """Capture the backend OpenAPI authority exactly once for one generation run."""
    return create_app(product_version=_source_product_version()).openapi()


def rendered_schema(document: dict[str, object] | None = None) -> str:
    return json.dumps(document or openapi_document(), indent=2, sort_keys=True) + "\n"


def write_openapi(
    document: dict[str, object] | None = None, *, output: Path = DEFAULT_OUTPUT
) -> Path:
    rendered = rendered_schema(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the current backend OpenAPI contract")
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write the schema to stdout instead of a file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="explicit schema output path",
    )
    args = parser.parse_args(argv)

    document = openapi_document()
    rendered = rendered_schema(document)
    if args.stdout:
        print(rendered, end="")
        return 0

    output = args.output if args.output.is_absolute() else ROOT / args.output
    write_openapi(document, output=output)
    print(f"Wrote {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
