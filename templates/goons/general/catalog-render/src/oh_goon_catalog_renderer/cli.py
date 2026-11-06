"""Native command for the source-free catalog renderer."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from oh_goon_catalog_renderer import (
    MAX_METADATA_BYTES,
    candidate_from_json,
    render_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render explicitly reviewed catalog metadata"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input", type=Path, help="Explicit bounded reviewed JSON file"
    )
    source.add_argument(
        "--environment",
        action="store_true",
        help="Consume only the explicitly supplied CATALOG_CANDIDATE_JSON value",
    )
    args = parser.parse_args()
    try:
        raw: bytes | str
        if args.environment:
            supplied = os.environ.get("CATALOG_CANDIDATE_JSON")
            if supplied is None:
                raise ValueError("reviewed metadata environment is missing")
            raw = supplied
        else:
            with args.input.open("rb") as stream:
                raw = stream.read(MAX_METADATA_BYTES + 1)
        candidate = candidate_from_json(raw)
        with Path("catalog-info.yaml").open(
            "x", encoding="utf-8", newline="\n"
        ) as output:
            output.write(render_catalog(candidate))
    except OSError:
        print(
            "Catalog candidate refused: metadata or new output is unavailable.",
            file=sys.stderr,
        )
        return 2
    except (ValueError, TypeError):
        # Values can contain secrets even when the field name is allowed.
        print(
            "Catalog candidate refused: invalid or oversized reviewed metadata.",
            file=sys.stderr,
        )
        return 2
    print("Created local catalog-info.yaml from explicitly reviewed metadata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
