"""Exact executed-membership evidence for bounded pytest verification units.

The durable correctness receipt remains ``MemberReceiptStore``.  This module records
run-local execution attempts and derives terminal membership coverage without changing
which member receipts are reusable.  Physical batching, retries, and timeout refinement
may create additional attempts, but they may never change the admitted logical set.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, TypedDict

from oh_goon.repository_tooling.pytest_member_identity import pytest_member_identity

ATTEMPT_SCHEMA = "oh-goon.pytest-member-attempt/v1"
EXTRA_SCHEMA = "oh-goon.pytest-extra-collected-item/v1"
MEMBERSHIP_SCHEMA = "oh-goon.pytest-executed-membership-receipt/v1"
RUN_SCHEMA = "oh-goon.pytest-execution-run/v1"
ATTEMPT_IDENTITY_SCHEMA = "oh-goon.pytest-execution-attempt/v1"
MEMBERSHIP_IDENTITY_SCHEMA = "oh-goon.pytest-logical-membership/v1"
_TERMINAL_STATUSES = frozenset({"PASSED", "FAILED", "NON_PASSED"})
PYTEST_NATIVE_OUTCOMES = frozenset(
    {"PASSED", "FAILED", "SKIPPED", "XFAILED", "XPASSED", "UNREPORTED"}
)


def _identity(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def logical_membership_identity(members: Iterable[str]) -> str:
    """Order-independent logical membership identity; physical layout is excluded."""

    return _identity({"schema": MEMBERSHIP_IDENTITY_SCHEMA, "members": sorted(members)})


def execution_run_identity(*, suite_identity: str, execution_plan_identity: str) -> str:
    return _identity(
        {
            "schema": RUN_SCHEMA,
            "suite_identity": suite_identity,
            "execution_plan_identity": execution_plan_identity,
            "nonce": uuid.uuid4().hex,
        }
    )


def execution_attempt_identity(
    *,
    run_identity: str,
    root_batch_identity: str,
    root_attempt: int,
    launch_ordinal: int,
    members: tuple[str, ...],
) -> str:
    return _identity(
        {
            "schema": ATTEMPT_IDENTITY_SCHEMA,
            "run_identity": run_identity,
            "root_batch_identity": root_batch_identity,
            "root_attempt": root_attempt,
            "launch_ordinal": launch_ordinal,
            "members": list(members),
        }
    )


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
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


@dataclass(frozen=True, slots=True)
class ExecutionMembershipReceipt:
    schema: str
    suite_identity: str
    execution_plan_identity: str
    execution_run_identity: str
    selected_membership_identity: str
    terminal_membership_identity: str | None
    selected: tuple[str, ...]
    attempted: tuple[str, ...]
    completed: tuple[str, ...]
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    non_passed: tuple[str, ...]
    resumed: tuple[str, ...]
    missing: tuple[str, ...]
    duplicates: tuple[str, ...]
    extras: tuple[str, ...]
    attempt_records: tuple[dict[str, object], ...]
    retry_bisection_lineage: tuple[dict[str, object], ...]
    terminal_success: bool
    effective_pytest_outcomes: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ExecutionMembershipStore:
    """Run-local attempt journal stored beside exact-context correctness receipts."""

    def __init__(
        self,
        directory: Path,
        *,
        suite_identity: str,
        execution_plan_identity: str,
        run_identity: str,
    ) -> None:
        self.directory = directory
        self.suite_identity = suite_identity
        self.execution_plan_identity = execution_plan_identity
        self.run_identity = run_identity

    @property
    def run_directory(self) -> Path:
        return self.directory / "execution-runs" / self.run_identity.removeprefix("sha256:")

    def _attempt_path(self, member: str, attempt_identity: str) -> Path:
        return (
            self.run_directory
            / "attempts"
            / attempt_identity.removeprefix("sha256:")
            / f"{pytest_member_identity(member).removeprefix('sha256:')}.json"
        )

    def record_started(
        self,
        member: str,
        *,
        attempt_identity: str,
        observed_batch_identity: str,
    ) -> Path:
        path = self._attempt_path(member, attempt_identity)
        if path.exists():
            return path
        _atomic_json_write(
            path,
            {
                "schema": ATTEMPT_SCHEMA,
                "suite_identity": self.suite_identity,
                "execution_plan_identity": self.execution_plan_identity,
                "execution_run_identity": self.run_identity,
                "attempt_identity": attempt_identity,
                "observed_batch_identity": observed_batch_identity,
                "member": member,
                "member_identity": pytest_member_identity(member),
                "status": "STARTED",
                "started_at_utc": datetime.now(UTC).isoformat(),
                "completed_at_utc": None,
            },
        )
        return path

    def record_terminal(
        self,
        member: str,
        *,
        attempt_identity: str,
        observed_batch_identity: str,
        status: str,
        pytest_outcomes: list[dict[str, str]] | None = None,
    ) -> Path:
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"unsupported pytest member terminal status: {status}")
        if pytest_outcomes is not None and any(
            not isinstance(row, dict)
            or set(row) != {"nodeid", "outcome"}
            or not isinstance(row["nodeid"], str) or not row["nodeid"]
            or row["outcome"] not in PYTEST_NATIVE_OUTCOMES
            for row in pytest_outcomes
        ):
            raise ValueError("unsupported native pytest item outcome")
        path = self.record_started(
            member,
            attempt_identity=attempt_identity,
            observed_batch_identity=observed_batch_identity,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "suite_identity": self.suite_identity,
            "execution_plan_identity": self.execution_plan_identity,
            "execution_run_identity": self.run_identity,
            "attempt_identity": attempt_identity,
            "observed_batch_identity": observed_batch_identity,
            "member": member,
            "member_identity": pytest_member_identity(member),
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise RuntimeError(
                "pytest execution-membership attempt identity changed before terminal write"
            )
        payload["status"] = status
        if pytest_outcomes is not None:
            payload["pytest_outcomes"] = pytest_outcomes
        payload["completed_at_utc"] = datetime.now(UTC).isoformat()
        _atomic_json_write(path, payload)
        return path

    def record_extra(self, nodeid: str, *, attempt_identity: str) -> Path:
        identity = _identity(
            {"schema": EXTRA_SCHEMA, "attempt_identity": attempt_identity, "nodeid": nodeid}
        )
        path = self.run_directory / "extras" / f"{identity.removeprefix('sha256:')}.json"
        _atomic_json_write(
            path,
            {
                "schema": EXTRA_SCHEMA,
                "suite_identity": self.suite_identity,
                "execution_plan_identity": self.execution_plan_identity,
                "execution_run_identity": self.run_identity,
                "attempt_identity": attempt_identity,
                "nodeid": nodeid,
                "extra_identity": identity,
            },
        )
        return path

    @staticmethod
    def _read_row(path: Path) -> dict[str, object] | None:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        return row if isinstance(row, dict) else None

    def _attempt_rows(self) -> tuple[dict[str, object], ...]:
        rows: list[dict[str, object]] = []
        attempt_root = self.run_directory / "attempts"
        if attempt_root.exists():
            for path in sorted(attempt_root.glob("*/*.json")):
                row = self._read_row(path)
                if row is None:
                    continue
                if (
                    row.get("schema") == ATTEMPT_SCHEMA
                    and row.get("suite_identity") == self.suite_identity
                    and row.get("execution_plan_identity") == self.execution_plan_identity
                    and row.get("execution_run_identity") == self.run_identity
                ):
                    rows.append(row)
        rows.sort(key=lambda row: (str(row.get("member")), str(row.get("attempt_identity"))))
        return tuple(rows)

    def passed_members(self) -> frozenset[str]:
        return frozenset(
            str(row["member"])
            for row in self._attempt_rows()
            if row.get("status") == "PASSED" and isinstance(row.get("member"), str)
        )

    def _extras(self) -> tuple[str, ...]:
        values: set[str] = set()
        extra_root = self.run_directory / "extras"
        if extra_root.exists():
            for path in sorted(extra_root.glob("*.json")):
                row = self._read_row(path)
                if row is None or row.get("execution_run_identity") != self.run_identity:
                    continue
                nodeid = row.get("nodeid")
                if isinstance(nodeid, str):
                    values.add(nodeid)
        return tuple(sorted(values))

    def finalize(
        self,
        *,
        selected: tuple[str, ...],
        resumed: tuple[str, ...],
        suite_passed: bool,
        recovery_lineage: tuple[dict[str, object], ...],
    ) -> tuple[ExecutionMembershipReceipt, Path]:
        rows = self._attempt_rows()
        selected_counts = Counter(selected)
        resumed_set = set(resumed)
        attempted_set = {str(row["member"]) for row in rows if isinstance(row.get("member"), str)}
        terminal_rows = [row for row in rows if row.get("status") in _TERMINAL_STATUSES]
        terminal_by_status = {
            status: {str(row["member"]) for row in terminal_rows if row.get("status") == status}
            for status in _TERMINAL_STATUSES
        }
        pass_counts = Counter(
            str(row["member"]) for row in terminal_rows if row.get("status") == "PASSED"
        )
        completed_set = resumed_set | {str(row["member"]) for row in terminal_rows}
        passed_set = resumed_set | terminal_by_status["PASSED"]
        # Execution membership proves that every admitted logical member reached a
        # pytest-terminal outcome accepted by the suite process.  Skips/xfails are
        # deliberately NON_PASSED (and therefore never reusable as correctness
        # receipts), but pytest exit 0 still closes run-local membership coverage.
        accepted_terminal_set = passed_set | terminal_by_status["NON_PASSED"]
        selected_set = set(selected)
        duplicates = {member for member, count in selected_counts.items() if count > 1} | {
            member for member, count in pass_counts.items() if count > 1
        }
        extras = set(self._extras()) | {
            member for member in attempted_set if member not in selected_set
        }
        missing = selected_set - completed_set
        selected_identity = logical_membership_identity(selected_set)
        terminal_success = (
            suite_passed
            and not missing
            and not duplicates
            and not extras
            and accepted_terminal_set == selected_set
            and completed_set == selected_set
        )
        # Use the SAME unique successful terminal row already accepted above.
        # Earlier interrupted attempts remain diagnostics, never a timestamp-based
        # replacement for the original correctness/member authority.
        by_member: dict[str, list[dict[str, object]]] = {}
        for row in terminal_rows:
            by_member.setdefault(str(row["member"]), []).append(row)
        effective: list[dict[str, object]] = []
        for member in sorted(selected_set):
            status = (
                "RESUMED"
                if member in resumed_set
                else "PASSED"
                if member in passed_set
                else "FAILED"
                if member in terminal_by_status["FAILED"]
                else "NON_PASSED"
                if member in terminal_by_status["NON_PASSED"]
                else "UNSTARTED"
            )
            accepted = [row for row in by_member.get(member, []) if row.get("status") == status]
            effective_row = accepted[0] if len(accepted) == 1 else None
            effective.append(
                {
                    "member": member,
                    "status": status,
                    "attempt_identity": effective_row.get("attempt_identity")
                    if effective_row
                    else None,
                    "pytest_outcomes": effective_row.get("pytest_outcomes", [])
                    if effective_row
                    else [],
                }
            )
        receipt = ExecutionMembershipReceipt(
            schema=MEMBERSHIP_SCHEMA,
            suite_identity=self.suite_identity,
            execution_plan_identity=self.execution_plan_identity,
            execution_run_identity=self.run_identity,
            selected_membership_identity=selected_identity,
            terminal_membership_identity=selected_identity if terminal_success else None,
            selected=tuple(sorted(selected_set)),
            attempted=tuple(sorted(attempted_set)),
            completed=tuple(sorted(completed_set)),
            passed=tuple(sorted(passed_set)),
            failed=tuple(sorted(terminal_by_status["FAILED"])),
            non_passed=tuple(sorted(terminal_by_status["NON_PASSED"])),
            resumed=tuple(sorted(resumed_set)),
            missing=tuple(sorted(missing)),
            duplicates=tuple(sorted(duplicates)),
            extras=tuple(sorted(extras)),
            attempt_records=rows,
            retry_bisection_lineage=recovery_lineage,
            terminal_success=terminal_success,
            effective_pytest_outcomes=tuple(effective),
        )
        path = self.run_directory / "membership-receipt.json"
        _atomic_json_write(path, receipt.as_dict())
        return receipt, path


@dataclass(frozen=True, slots=True)
class PytestExecutionMembershipContext:
    directory: Path | None
    suite_identity: str | None
    execution_plan_identity: str | None
    run_identity: str | None
    root_batch_identity: str
    root_attempt: int


class PytestReceiptOptions(TypedDict, total=False):
    receipt_directory: Path | None
    receipt_suite_identity: str | None
    receipt_execution_plan_identity: str | None
    receipt_run_identity: str | None
    receipt_attempt_identity: str | None


@dataclass(frozen=True, slots=True)
class PreparedPytestAttempt:
    members: tuple[str, ...]
    reused_prefix: str
    receipt_kwargs: PytestReceiptOptions


class PytestExecutionMembershipSession:
    """Bridge one runner invocation to run-local membership evidence."""

    def __init__(self, context: PytestExecutionMembershipContext) -> None:
        self.context = context
        self._launch_ordinal = 0
        self._store = self._build_store()

    def _build_store(self) -> ExecutionMembershipStore | None:
        values = (
            self.context.directory,
            self.context.suite_identity,
            self.context.execution_plan_identity,
            self.context.run_identity,
        )
        if not all(values):
            return None
        return ExecutionMembershipStore(
            self.context.directory,  # type: ignore[arg-type]
            suite_identity=self.context.suite_identity,  # type: ignore[arg-type]
            execution_plan_identity=self.context.execution_plan_identity,  # type: ignore[arg-type]
            run_identity=self.context.run_identity,  # type: ignore[arg-type]
        )

    def prepare(
        self, members: tuple[str, ...], *, allow_current_run_reuse: bool
    ) -> PreparedPytestAttempt:
        remaining, prefix = self._remaining_members(members, allow_current_run_reuse)
        if not remaining:
            return PreparedPytestAttempt((), prefix, {})
        self._launch_ordinal += 1
        return PreparedPytestAttempt(remaining, prefix, self._receipt_kwargs(remaining))

    def _remaining_members(
        self, members: tuple[str, ...], allow_current_run_reuse: bool
    ) -> tuple[tuple[str, ...], str]:
        if self._store is None or not allow_current_run_reuse:
            return members, ""
        passed = self._store.passed_members()
        remaining = tuple(member for member in members if member not in passed)
        if remaining == members:
            return members, ""
        reused = len(members) - len(remaining)
        return (
            remaining,
            f"BATCH RECOVERY MEMBERSHIP: reusing {reused} current-run completed members\n",
        )

    def _receipt_kwargs(self, members: tuple[str, ...]) -> PytestReceiptOptions:
        context = self.context
        if self._store is None:
            return {}
        attempt_identity = execution_attempt_identity(
            run_identity=context.run_identity,  # type: ignore[arg-type]
            root_batch_identity=context.root_batch_identity,
            root_attempt=context.root_attempt,
            launch_ordinal=self._launch_ordinal,
            members=members,
        )
        return {
            "receipt_directory": context.directory,
            "receipt_suite_identity": context.suite_identity,
            "receipt_execution_plan_identity": context.execution_plan_identity,
            "receipt_run_identity": context.run_identity,
            "receipt_attempt_identity": attempt_identity,
        }
