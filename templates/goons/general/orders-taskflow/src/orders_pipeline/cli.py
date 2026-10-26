"""One CLI command per independently observable operation."""

import argparse
import json
from pathlib import Path
from .operations import extract_fixture, load, transform


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("extract").add_argument(
        "--fixture", choices=["official", "changed", "invalid"], required=True
    )
    sub.add_parser("transform").add_argument("--data", required=True)
    sub.add_parser("load").add_argument("--total", required=True)
    args = parser.parse_args()
    output = Path(".showcase")
    output.mkdir(exist_ok=True)
    if args.command == "extract":
        data = extract_fixture(args.fixture)
        (output / "extracted.json").write_text(
            json.dumps(data, separators=(",", ":")) + "\n", encoding="utf8"
        )
    elif args.command == "transform":
        total = transform(json.loads(args.data))
        (output / "total.txt").write_text(total + "\n", encoding="utf8")
    else:
        result = load(args.total)
        (output / "result.json").write_text(json.dumps(result) + "\n", encoding="utf8")
        print("Total order value is:", result["total_order_value"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
