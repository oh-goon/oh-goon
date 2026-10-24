from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from oh_goon.integrations.hashmarks.verification_selection import (
    load_hashmarks_verification_selection,
)
from oh_goon.repository_tooling.hashmarks_verification_execution import (
    HashmarksVerificationExecutionReceipt,
    VerificationExecutionGroupingPolicy,
    build_verification_execution_plan,
    build_verification_execution_receipt,
)
from oh_goon.runtime.execution.shards import (
    ShardExecutionContext,
    ShardExecutionReport,
    ShardResult,
    ShardRetryPolicy,
    execute_shard_plan,
)


def _sha256_identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runner_toolchain_identity() -> str:
    executable = Path(sys.executable).resolve()
    return _sha256_identity(
        {
            "python_version": sys.version,
            "python_executable_sha256": _file_sha256(executable),
            "pytest_version": importlib.metadata.version("pytest"),
        }
    )


def _installed_dependency_identity() -> str:
    packages = sorted(
        {
            (dist.metadata.get("Name") or "").strip().lower(): dist.version
            for dist in importlib.metadata.distributions()
            if (dist.metadata.get("Name") or "").strip()
        }.items()
    )
    return _sha256_identity({"installed_python_distributions": packages})


def _default_environment_identity() -> str:
    return _sha256_identity(
        {
            "declared_ambient_environment_inputs": [],
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
        }
    )


def _iter_results(results: Iterable[ShardResult]) -> Iterator[ShardResult]:
    for result in results:
        yield result
        yield from _iter_results(result.children)


def _default_state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "oh-goon" / "hashmarks-verification"
    return Path.home() / ".local" / "state" / "oh-goon" / "hashmarks-verification"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute an exact Hashmarks downstream verification selection "
            "under Oh-Goon-owned batching and execution authority"
        )
    )
    parser.add_argument("contract", type=Path)
    parser.add_argument("envelope", type=Path)
    parser.add_argument("--admitted-repository-identity", required=True)
    parser.add_argument("--admitted-source-identity", required=True)
    parser.add_argument("--producer-artifact-identity", required=True)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--shard-timeout", type=float, default=180.0)
    parser.add_argument("--suite-timeout", type=float)
    parser.add_argument("--minimum-execution-window", type=float, default=1.0)
    parser.add_argument("--no-timeout-refinement", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--retry-exit-code", action="append", type=int, default=[])
    parser.add_argument("--retry-timeouts", action="store_true")
    parser.add_argument("--retry-environment", action="store_true")
    parser.add_argument("--retry-reason", default="")
    parser.add_argument("--retry-delay", action="append", type=float, default=[])
    parser.add_argument("--state-root", type=Path, default=_default_state_root())
    parser.add_argument("--runner-toolchain-identity")
    parser.add_argument("--dependency-identity")
    parser.add_argument("--environment-identity")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--pytest-arg", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report-path", type=Path)
    return parser


def _execute(
    args: argparse.Namespace,
) -> tuple[HashmarksVerificationExecutionReceipt, ShardExecutionReport]:
    selection = load_hashmarks_verification_selection(
        args.contract,
        args.envelope,
        admitted_repository_identity=args.admitted_repository_identity,
        admitted_source_identity=args.admitted_source_identity,
        expected_producer_artifact_identity=args.producer_artifact_identity,
    )
    plan = build_verification_execution_plan(
        selection,
        command_prefix=(sys.executable, "-m", "pytest", *tuple(args.pytest_arg)),
        grouping_policy=VerificationExecutionGroupingPolicy(args.batch_size),
    )
    context = ShardExecutionContext(
        runner_toolchain_identity=args.runner_toolchain_identity or _runner_toolchain_identity(),
        dependency_identity=args.dependency_identity or _installed_dependency_identity(),
        environment_identity=args.environment_identity or _default_environment_identity(),
    )
    policy = ShardRetryPolicy(
        max_attempts=args.max_attempts,
        retryable_exit_codes=tuple(args.retry_exit_code),
        retry_timeouts=args.retry_timeouts,
        retry_environment_failures=args.retry_environment,
        reason=args.retry_reason,
        delays_seconds=tuple(args.retry_delay),
    )
    report = execute_shard_plan(
        plan,
        cwd=args.repository.resolve(),
        workers=args.workers,
        shard_timeout_seconds=args.shard_timeout,
        suite_timeout_seconds=args.suite_timeout,
        retry_policy=policy,
        state_root=args.state_root,
        resume=not args.no_resume,
        execution_context=context,
        timeout_refinement=not args.no_timeout_refinement,
        minimum_execution_window_seconds=args.minimum_execution_window,
    )
    return build_verification_execution_receipt(selection, report), report


def _publish_receipt(
    args: argparse.Namespace, receipt: HashmarksVerificationExecutionReceipt
) -> None:
    rendered = receipt.to_json()
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.report_path.with_suffix(args.report_path.suffix + ".tmp")
        tmp.write_text(rendered + "\n", encoding="utf-8")
        tmp.replace(args.report_path)
    if args.json:
        print(rendered)
        return
    print("=== HASHMARKS VERIFICATION EXECUTION ===")
    print(f"Hashmarks: {receipt.hashmarks_identity}")
    print(f"Selection: {receipt.selected_membership_identity}")
    print(f"Selected: {len(receipt.selected)}")
    print(f"Attempted: {len(receipt.attempted)}")
    print(f"Completed: {len(receipt.completed)}")
    print(f"Missing: {len(receipt.missing)}")
    print(f"Duplicates: {len(receipt.duplicates)}")
    print(f"Extras: {len(receipt.extras)}")
    print(f"Receipt: {receipt.receipt_identity}")


def _exit_code(receipt: HashmarksVerificationExecutionReceipt, report: ShardExecutionReport) -> int:
    if receipt.terminal_success:
        return 0
    blocked = {"TIMEOUT", "SUITE_TIMEOUT", "ENVIRONMENT_BLOCKED", "DEADLINE_RESERVED"}
    return 2 if any(result.status in blocked for result in _iter_results(report.results)) else 1


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        receipt, report = _execute(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    _publish_receipt(args, receipt)
    return _exit_code(receipt, report)


if __name__ == "__main__":
    raise SystemExit(main())
