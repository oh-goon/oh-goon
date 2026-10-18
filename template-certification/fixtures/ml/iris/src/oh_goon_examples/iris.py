from __future__ import annotations

import argparse


def main() -> int:
    """Expose the real project entrypoint without performing model training."""

    parser = argparse.ArgumentParser(prog="iris-train")
    parser.add_argument("--random-seed", type=int, required=True)
    parser.add_argument("--test-size-percent", type=int, required=True)
    parser.add_argument("--minimum-accuracy-percent", type=int, required=True)
    parser.add_argument("--max-iterations", type=int, required=True)
    parser.parse_args()
    return 0
