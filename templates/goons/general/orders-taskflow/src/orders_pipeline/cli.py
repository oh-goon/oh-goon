"""Installed commands for the shared operations and their direct composition."""

import argparse
import json
from pathlib import Path
from .operations import extract_fixture, load, transform


def _extract(output, fixture):
    data = extract_fixture(fixture)
    (output / "extracted.json").write_text(
        json.dumps(data, separators=(",", ":")) + "\n", encoding="utf8"
    )
    return data


def _transform(output, data):
    total = transform(data)
    (output / "total.txt").write_text(total + "\n", encoding="utf8")
    return total


def _load(output, total):
    result = load(total)
    (output / "result.json").write_text(json.dumps(result) + "\n", encoding="utf8")
    print("Total order value is:", result["total_order_value"])


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("extract").add_argument(
        "--fixture", choices=["official", "changed", "invalid"], required=True
    )
    sub.add_parser("transform").add_argument("--data", required=True)
    sub.add_parser("load").add_argument("--total", required=True)
    sub.add_parser("run").add_argument(
        "--fixture", choices=["official", "changed", "invalid"], required=True
    )
    args = parser.parse_args()
    output = Path(".showcase")
    output.mkdir(exist_ok=True)
    if args.command == "extract":
        _extract(output, args.fixture)
    elif args.command == "transform":
        _transform(output, json.loads(args.data))
    elif args.command == "load":
        _load(output, args.total)
    else:
        _load(output, _transform(output, _extract(output, args.fixture)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
