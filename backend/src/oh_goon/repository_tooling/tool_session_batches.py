"""Resume-safe bounded qualification units for tool-limited development sessions.

This is a transport/iteration helper only. It does not define certification truth.
Every unit delegates to an existing authoritative test or repository-owned check and
records a PASS receipt bound to exact source, plan, and Python toolchain identity.
Re-running the command skips only exact-identity PASS units, so an outer tool/session
cutoff loses at most the currently executing unit instead of the entire qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity
from oh_goon.repository_tooling.test_batch_receipts import python_toolchain_identity

from oh_goon.core.file_digest import sha256_file

RECEIPT_SCHEMA = "oh-goon.tool-session-unit-receipt/v2"
PLAN_SCHEMA = "oh-goon.tool-session-plan/v1"
STATUS_SCHEMA = "oh-goon.tool-session-status/v1"


@dataclass(frozen=True, slots=True)
class QualificationUnit:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int
    expected_runtime_seconds: float = 30.0
    environment: tuple[tuple[str, str], ...] = (("TERM", "dumb"),)


def _python() -> str:
    return sys.executable


def maintenance_release_units() -> tuple[QualificationUnit, ...]:
    python = _python()
    return (
        QualificationUnit(
            "structural-authority",
            (python, "-m", "pytest", "-q", "scripts/tests/test_structural_authority_contract.py"),
            180,
            10.0,
        ),
        QualificationUnit(
            "shard-contract",
            (python, "-m", "pytest", "-q", "scripts/tests/test_shard_execution_contract.py"),
            120,
            8.0,
        ),
        QualificationUnit(
            "hashmarks-boundary",
            (python, "-m", "pytest", "-q", "scripts/tests/test_hashmarks_harness_boundary_contract.py"),
            180,
            30.0,
        ),
        QualificationUnit(
            "repository-understanding",
            (python, "-m", "pytest", "-q", "scripts/tests/test_repository_understanding_certification.py"),
            1200,
            90.0,
        ),
        QualificationUnit(
            "architecture",
            (python, "-m", "oh_goon.repository_tooling.check_architecture"),
            180,
            30.0,
        ),
        QualificationUnit(
            "generated-contracts",
            (python, "-m", "oh_goon.repository_tooling.generation.check_generated"),
            120,
            15.0,
        ),
        QualificationUnit(
            "release-identity",
            (python, "-m", "oh_goon.repository_tooling.release.stamp_release_version", "check"),
            120,
            8.0,
        ),
        QualificationUnit(
            "goon-static-admission",
            (python, "-m", "oh_goon.repository_tooling.goon_static_admission"),
            120,
            5.0,
        ),
        QualificationUnit(
            "compileall",
            (python, "-m", "compileall", "-q", "backend/src", "scripts"),
            120,
            5.0,
        ),
    )


def _identity(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _plan_identity(units: tuple[QualificationUnit, ...]) -> str:
    return _identity({
        "schema": PLAN_SCHEMA,
        "units": [
            {
                "name": unit.name,
                "command": list(unit.command),
                "timeout_seconds": unit.timeout_seconds,
                "expected_runtime_seconds": unit.expected_runtime_seconds,
                "environment": [list(pair) for pair in unit.environment],
            }
            for unit in units
        ],
    })


def _state_root() -> Path:
    configured = os.environ.get("OH_GOON_TOOL_SESSION_STATE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    base = os.environ.get("XDG_STATE_HOME")
    state_home = Path(base).expanduser() if base else Path.home() / ".local" / "state"
    return state_home / "oh-goon" / "tool-session-qualification" / "schema-1"


def _receipt_dir(*, source_identity: str, plan_identity: str, toolchain_identity: str) -> Path:
    key = _identity({
        "source_identity": source_identity,
        "plan_identity": plan_identity,
        "toolchain_identity": toolchain_identity,
    }).removeprefix("sha256:")
    return _state_root() / key


def _receipt_path(directory: Path, unit: QualificationUnit) -> Path:
    return directory / f"{unit.name}.json"


def _receipt_valid(path: Path, *, source_identity: str, plan_identity: str, toolchain_identity: str, unit: QualificationUnit) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {
        "schema": RECEIPT_SCHEMA,
        "source_identity": source_identity,
        "plan_identity": plan_identity,
        "toolchain_identity": toolchain_identity,
        "unit": unit.name,
        "command": list(unit.command),
        "timeout_seconds": unit.timeout_seconds,
        "expected_runtime_seconds": unit.expected_runtime_seconds,
        "environment": [list(pair) for pair in unit.environment],
        "observed_runtime_seconds": payload.get("observed_runtime_seconds"),
        "result": "PASS",
        "completed_utc": payload.get("completed_utc"),
    } and isinstance(payload.get("completed_utc"), str) and isinstance(payload.get("observed_runtime_seconds"), (int, float)) and payload.get("observed_runtime_seconds", -1) >= 0


def _write_receipt(path: Path, *, source_identity: str, plan_identity: str, toolchain_identity: str, unit: QualificationUnit, observed_runtime_seconds: float) -> None:
    payload = {
        "schema": RECEIPT_SCHEMA,
        "source_identity": source_identity,
        "plan_identity": plan_identity,
        "toolchain_identity": toolchain_identity,
        "unit": unit.name,
        "command": list(unit.command),
        "timeout_seconds": unit.timeout_seconds,
        "expected_runtime_seconds": unit.expected_runtime_seconds,
        "environment": [list(pair) for pair in unit.environment],
        "observed_runtime_seconds": round(observed_runtime_seconds, 3),
        "result": "PASS",
        "completed_utc": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _run_unit(unit: QualificationUnit) -> int:
    print(f"=== TOOL SESSION QUALIFICATION UNIT: {unit.name} ===", flush=True)
    print("Command: " + " ".join(unit.command), flush=True)
    try:
        result = run_command(
            *unit.command,
            cwd=ROOT,
            timeout=float(unit.timeout_seconds),
            merge_stderr=True,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            env=dict(unit.environment),
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        if output:
            print(output, end="" if str(output).endswith("\n") else "\n")
        print(f"UNIT TIMEOUT after {unit.timeout_seconds}s: {unit.name}", file=sys.stderr)
        return 124
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    return result.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="show exact-identity unit progress without executing")
    parser.add_argument("--status-json", action="store_true", help="emit machine-readable exact-identity progress without executing")
    parser.add_argument("--max-units", type=int, default=1, help="maximum pending units to execute this invocation")
    parser.add_argument("--session-budget-seconds", type=float, default=None, help="soft wall-clock budget for this invocation; units that cannot fit are not started")
    parser.add_argument("--checkpoint-reserve-seconds", type=float, default=30.0, help="budget reserved for receipt/status persistence before soft close")
    return parser



def _validate_args(args: argparse.Namespace) -> None:
    if args.max_units < 1:
        raise SystemExit("--max-units must be >= 1")
    if args.session_budget_seconds is not None and args.session_budget_seconds <= 0:
        raise SystemExit("--session-budget-seconds must be > 0")
    if args.checkpoint_reserve_seconds < 0:
        raise SystemExit("--checkpoint-reserve-seconds must be >= 0")


def _receipt_hash(path: Path) -> str | None:
    try:
        return "sha256:" + sha256_file(path)
    except OSError:
        return None


def _status_payload(
    *, units: tuple[QualificationUnit, ...], completed: list[QualificationUnit], pending: list[QualificationUnit],
    directory: Path, source_identity: str, plan_identity: str, toolchain_identity: str,
) -> dict[str, object]:
    completed_names = {unit.name for unit in completed}
    return {
        "schema": STATUS_SCHEMA,
        "source_identity": source_identity,
        "plan_identity": plan_identity,
        "toolchain_identity": toolchain_identity,
        "first_unproven_owner": pending[0].name if pending else None,
        "units": [
            {
                "owner": unit.name,
                "state": "PASS" if unit.name in completed_names else "PENDING",
                "receipt_hash": _receipt_hash(_receipt_path(directory, unit)) if unit.name in completed_names else None,
            }
            for unit in units
        ],
    }


def _handle_early_exit(
    *, status: bool, status_json: bool, units: tuple[QualificationUnit, ...], completed: list[QualificationUnit],
    pending: list[QualificationUnit], directory: Path, source_identity: str, plan_identity: str, toolchain_identity: str,
) -> bool:
    if status_json:
        print(json.dumps(_status_payload(
            units=units, completed=completed, pending=pending, directory=directory, source_identity=source_identity,
            plan_identity=plan_identity, toolchain_identity=toolchain_identity,
        ), sort_keys=True))
        return True
    if status:
        for unit in units:
            state = "PASS" if unit in completed else "PENDING"
            print(f"{state:7} {unit.name}")
        return True
    if not pending:
        print("TOOL SESSION QUALIFICATION COMPLETE")
        return True
    return False

def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    session_started = time.monotonic()
    units = maintenance_release_units()
    source_identity = canonical_source_identity(ROOT)
    plan_identity = _plan_identity(units)
    toolchain_identity = python_toolchain_identity()
    directory = _receipt_dir(
        source_identity=source_identity,
        plan_identity=plan_identity,
        toolchain_identity=toolchain_identity,
    )
    completed: list[QualificationUnit] = []
    for unit in units:
        if not _receipt_valid(
            _receipt_path(directory, unit),
            source_identity=source_identity,
            plan_identity=plan_identity,
            toolchain_identity=toolchain_identity,
            unit=unit,
        ):
            break
        completed.append(unit)
    print(
        f"TOOL SESSION QUALIFICATION progress={len(completed)}/{len(units)} "
        f"source={source_identity[:19]}... plan={plan_identity[:19]}...",
        flush=True,
    )
    pending = [unit for unit in units if unit not in completed]
    if _handle_early_exit(
        status=args.status, status_json=args.status_json, units=units, completed=completed, pending=pending,
        directory=directory, source_identity=source_identity, plan_identity=plan_identity, toolchain_identity=toolchain_identity,
    ):
        return 0
    executed = 0
    for unit in pending[: args.max_units]:
        if args.session_budget_seconds is not None:
            elapsed = time.monotonic() - session_started
            remaining_budget = args.session_budget_seconds - elapsed
            required_budget = unit.expected_runtime_seconds + args.checkpoint_reserve_seconds
            if remaining_budget < required_budget:
                print(
                    f"TOOL SESSION SOFT STOP unit={unit.name} remaining={remaining_budget:.1f}s "
                    f"required={required_budget:.1f}s estimate={unit.expected_runtime_seconds:.1f}s "
                    f"reserve={args.checkpoint_reserve_seconds:.1f}s; unit not started",
                    flush=True,
                )
                break
        unit_started = time.monotonic()
        code = _run_unit(unit)
        observed_runtime_seconds = time.monotonic() - unit_started
        if code != 0:
            print(f"TOOL SESSION QUALIFICATION FAILED unit={unit.name} exit={code}", file=sys.stderr)
            return code
        _write_receipt(
            _receipt_path(directory, unit),
            source_identity=source_identity,
            plan_identity=plan_identity,
            toolchain_identity=toolchain_identity,
            unit=unit,
            observed_runtime_seconds=observed_runtime_seconds,
        )
        executed += 1
        print(
            f"TOOL SESSION QUALIFICATION PASS unit={unit.name} "
            f"observed={observed_runtime_seconds:.2f}s estimate={unit.expected_runtime_seconds:.1f}s",
            flush=True,
        )
    remaining = len(pending) - executed
    if remaining:
        print(f"TOOL SESSION QUALIFICATION REMAINING units={remaining}; rerun the same command")
    else:
        print("TOOL SESSION QUALIFICATION COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
