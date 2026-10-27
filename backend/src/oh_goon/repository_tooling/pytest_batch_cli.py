"""Command-line contract for the bounded pytest batch runner."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_batch_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--qualified",
        action="store_true",
        help="use the bound isolated project interpreter and native plugins",
    )
    parser.add_argument("--pytest-config", type=Path, default=Path("backend/pyproject.toml"))
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="native option, repeated as --pytest-arg=VALUE",
    )
    parser.add_argument(
        "--show-output", action="store_true", help="show native output and durations for each batch"
    )
    parser.add_argument(
        "--coverage-source",
        action="append",
        default=[],
        help="native branch-coverage source, repeatable",
    )
    parser.add_argument("--coverage-data-file", type=Path, default=None)
    parser.add_argument("--coverage-report", action="append", default=[])
    parser.add_argument("--coverage-fail-under", type=float, default=None)
    parser.add_argument("--granularity", choices=("files", "tests"), default="files")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-timeout", type=int, default=120)
    parser.add_argument(
        "--suite-timeout",
        type=int,
        default=None,
        help="hard wall-clock budget for the complete batch run",
    )
    parser.add_argument("--batch", type=int, default=None, help="run only one 1-based batch")
    parser.add_argument("--marker", default="not certification")
    parser.add_argument(
        "--no-timeout-bisection",
        action="store_true",
        help="disable deterministic recovery that bisects timed-out multi-member batches",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="atomically write bounded batch execution evidence JSON",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse exact-identity non-failing member receipts from earlier interrupted runs",
    )
    parser.add_argument(
        "--resume-key",
        default="default",
        help="stable logical lane name used to partition durable member receipts",
    )
    parser.add_argument(
        "--resume-state-root",
        type=Path,
        default=None,
        help="override the XDG-backed durable member-receipt root",
    )
    parser.add_argument(
        "--resume-context",
        action="append",
        default=[],
        help="additional exact environment identity token; repeat for extra lane authorities",
    )
    parser.add_argument(
        "--environment-retries",
        type=int,
        default=0,
        help="retry only classified host process/thread exhaustion this many times",
    )
    args = parser.parse_args()
    _validate_batch_args(parser, args)
    if any("::" in str(path) for path in args.paths):
        args.granularity = "tests"
    return parser, args


def _validate_batch_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    coverage_requested = bool(
        args.coverage_source
        or args.coverage_data_file is not None
        or args.coverage_report
        or args.coverage_fail_under is not None
    )
    if coverage_requested and (args.resume or args.batch is not None):
        parser.error(
            "coverage requires a complete fresh run; --resume and --batch provide no admitted coverage data"
        )
    if args.batch_size < 1 or args.workers < 1 or args.batch_timeout < 1:
        parser.error("batch-size, workers, and batch-timeout must be positive")
    if args.environment_retries < 0:
        parser.error("environment-retries must be >= 0")
    if args.suite_timeout is not None and args.suite_timeout < 1:
        parser.error("suite-timeout must be positive")
    if args.suite_timeout is not None and args.suite_timeout <= args.batch_timeout:
        parser.error(
            "suite-timeout must exceed batch-timeout to preserve startup/checkpoint headroom"
        )
