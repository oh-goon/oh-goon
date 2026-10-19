"""Pytest plugin that persists one atomic receipt per admitted batch member.

Loaded by ``oh-goon-test-batches`` for exact execution evidence; ``--resume`` controls reuse. The runner's durable unit is its
member: a whole test file for file granularity, or one native test selector for test
granularity. A member receipt is written only after every collected pytest item beneath
that member completed without a failing setup/call/teardown report. Therefore a host
kill can lose only the currently incomplete member, not earlier siblings in the batch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling.pytest_execution_membership import ExecutionMembershipStore
from oh_goon.repository_tooling.pytest_member_selection import (
    collected_item,
    read_native_collection,
)
from oh_goon.repository_tooling.test_batch_receipts import MemberReceiptStore

_STORE: MemberReceiptStore | None = None
_EXECUTION_STORE: ExecutionMembershipStore | None = None
_ATTEMPT_IDENTITY: str | None = None
_BATCH_IDENTITY: str | None = None
_EXECUTION_PLAN_IDENTITY: str | None = None
_EXPECTED_MEMBERS: tuple[str, ...] = ()
_ADMISSION_ROOT = Path.cwd()
_EXPECTED_COUNTS: dict[str, int] = {}
_STATE: dict[str, dict[str, Any]] = {}
_ITEM_STATE: dict[str, bool] = {}
_NODE_TO_MEMBER: dict[str, str] = {}


def pytest_configure(config: Any) -> None:
    global _STORE, _EXECUTION_STORE, _ATTEMPT_IDENTITY
    global _BATCH_IDENTITY, _EXECUTION_PLAN_IDENTITY, _EXPECTED_MEMBERS, _ADMISSION_ROOT
    directory = os.environ.get("OH_GOON_PYTEST_RECEIPT_DIRECTORY")
    suite_identity = os.environ.get("OH_GOON_PYTEST_RECEIPT_SUITE_IDENTITY")
    batch = os.environ.get("OH_GOON_PYTEST_RECEIPT_BATCH_IDENTITY")
    execution_plan = os.environ.get("OH_GOON_PYTEST_RECEIPT_EXECUTION_PLAN_IDENTITY")
    execution_run = os.environ.get("OH_GOON_PYTEST_RECEIPT_RUN_IDENTITY")
    attempt_identity = os.environ.get("OH_GOON_PYTEST_RECEIPT_ATTEMPT_IDENTITY")
    raw_members = os.environ.get("OH_GOON_PYTEST_RECEIPT_MEMBERS")
    root = os.environ.get("OH_GOON_PYTEST_RECEIPT_ROOT")
    if not directory or not suite_identity or not batch or not raw_members:
        return
    try:
        members = json.loads(raw_members)
    except json.JSONDecodeError:
        return
    if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
        return
    _STORE = MemberReceiptStore(Path(directory), suite_identity=suite_identity)
    _BATCH_IDENTITY = batch
    _EXECUTION_PLAN_IDENTITY = execution_plan
    _ATTEMPT_IDENTITY = attempt_identity
    _EXPECTED_MEMBERS = tuple(members)
    if execution_plan and execution_run and attempt_identity:
        _EXECUTION_STORE = ExecutionMembershipStore(
            Path(directory),
            suite_identity=suite_identity,
            execution_plan_identity=execution_plan,
            run_identity=execution_run,
        )
    _ADMISSION_ROOT = Path(root).resolve() if root else Path.cwd().resolve()


def _resolved_member_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (_ADMISSION_ROOT / path).resolve()


def _member_for_item(item: Any) -> str | None:
    try:
        item_path = Path(item.path).resolve()
    except (AttributeError, OSError, TypeError):
        return None
    _prefix, separator, item_selector = item.nodeid.partition("::")
    for member in _EXPECTED_MEMBERS:
        member_path_text, member_separator, member_selector = member.partition("::")
        try:
            if _resolved_member_path(member_path_text) != item_path:
                continue
        except OSError:
            continue
        if not member_separator:
            return member
        if not separator:
            continue
        if item_selector == member_selector or item_selector.startswith(member_selector + "["):
            return member
    return None


def pytest_collection_finish(session: Any) -> None:
    _EXPECTED_COUNTS.clear()
    _STATE.clear()
    _ITEM_STATE.clear()
    _NODE_TO_MEMBER.clear()
    collection_path = os.environ.get("OH_GOON_PYTEST_RECEIPT_COLLECTION")
    if collection_path:
        import pytest

        packet = read_native_collection(Path(collection_path))
        actual = {collected_item(item, _ADMISSION_ROOT)["selector"] for item in session.items}
        file_members = {member for member in _EXPECTED_MEMBERS if "::" not in member}
        node_members = {member for member in _EXPECTED_MEMBERS if "::" in member}
        expected = {
            row["selector"]
            for row in packet["selected"]
            if row["file"] in file_members or row["selector"] in node_members
        }
        if actual != expected:
            raise pytest.UsageError("Native pytest collection changed after batch admission")
    for item in session.items:
        member = _member_for_item(item)
        if member is None:
            if _EXECUTION_STORE is not None and _ATTEMPT_IDENTITY is not None:
                _EXECUTION_STORE.record_extra(item.nodeid, attempt_identity=_ATTEMPT_IDENTITY)
            continue
        _NODE_TO_MEMBER[item.nodeid] = member
        _EXPECTED_COUNTS[member] = _EXPECTED_COUNTS.get(member, 0) + 1
        _STATE.setdefault(member, {"completed": 0, "failed": False, "non_passed": False})


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    del location
    if _EXECUTION_STORE is None or _ATTEMPT_IDENTITY is None or _BATCH_IDENTITY is None:
        return
    member = _NODE_TO_MEMBER.get(nodeid)
    if member is None:
        return
    _EXECUTION_STORE.record_started(
        member,
        attempt_identity=_ATTEMPT_IDENTITY,
        observed_batch_identity=_BATCH_IDENTITY,
    )


def _observe_report(report: Any, state: dict[str, Any]) -> None:
    if report.failed:
        state["failed"] = True
        _ITEM_STATE[report.nodeid] = True
    elif report.skipped or getattr(report, "wasxfail", None) is not None:
        state["non_passed"] = True
        _ITEM_STATE[report.nodeid] = True
    else:
        _ITEM_STATE.setdefault(report.nodeid, False)


def _terminal_status(state: dict[str, Any]) -> str:
    if state["failed"]:
        return "FAILED"
    if state["non_passed"]:
        return "NON_PASSED"
    return "PASSED"


def _record_execution_terminal(member: str, status: str) -> None:
    if _EXECUTION_STORE is None or _ATTEMPT_IDENTITY is None or _BATCH_IDENTITY is None:
        return
    _EXECUTION_STORE.record_terminal(
        member,
        attempt_identity=_ATTEMPT_IDENTITY,
        observed_batch_identity=_BATCH_IDENTITY,
        status=status,
    )


def pytest_runtest_logreport(report: Any) -> None:
    if _STORE is None or _BATCH_IDENTITY is None:
        return
    member = _NODE_TO_MEMBER.get(report.nodeid)
    if member is None or member not in _EXPECTED_COUNTS:
        return
    state = _STATE[member]
    _observe_report(report, state)
    if report.when != "teardown":
        return
    state["completed"] += 1
    if state["completed"] != _EXPECTED_COUNTS[member]:
        return
    status = _terminal_status(state)
    _record_execution_terminal(member, status)
    if status == "PASSED":
        _STORE.record(
            member,
            outcome="PYTEST_MEMBER_PASSED",
            execution_plan_identity=_EXECUTION_PLAN_IDENTITY,
            observed_batch_identity=_BATCH_IDENTITY,
        )
