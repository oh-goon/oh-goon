from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import oh_goon.repository_tooling.pytest_member_identity as member_identity_owner
from oh_goon.repository_tooling.pytest_execution_membership import (
    ExecutionMembershipStore,
)
from oh_goon.repository_tooling.pytest_member_identity import pytest_member_identity
from oh_goon.repository_tooling.test_batch_receipts import MemberReceiptStore


def test_corrupt_receipt_cannot_be_reused(tmp_path: Path) -> None:
    store = MemberReceiptStore(tmp_path, suite_identity="sha256:" + "a" * 64)
    member = "tests/test_example.py::test_behavior"
    path = store.record(member, outcome="PYTEST_MEMBER_PASSED")
    assert store.reusable(member)

    path.write_text("[]", encoding="utf-8")

    assert not store.reusable(member)


@pytest.mark.parametrize(
    "member",
    (
        "tests/test_file.py",
        "tests/test_file.py::test_case",
        "tests/test_file.py::test_case[param-1]",
        "tests/test_unicode.py::test_räksmörgås[åäö]",
    ),
)
def test_receipt_and_execution_membership_share_exact_pytest_member_identity(
    tmp_path: Path,
    member: str,
) -> None:
    expected = pytest_member_identity(member)

    receipt_store = MemberReceiptStore(
        tmp_path / "receipts",
        suite_identity="sha256:" + "a" * 64,
    )
    receipt_path = receipt_store.record(
        member,
        outcome="PYTEST_MEMBER_PASSED",
        execution_plan_identity="sha256:" + "b" * 64,
        observed_batch_identity="sha256:" + "c" * 64,
    )
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))

    execution_store = ExecutionMembershipStore(
        tmp_path / "execution",
        suite_identity="sha256:" + "a" * 64,
        execution_plan_identity="sha256:" + "b" * 64,
        run_identity="sha256:" + "d" * 64,
    )
    attempt_path = execution_store.record_started(
        member,
        attempt_identity="sha256:" + "e" * 64,
        observed_batch_identity="sha256:" + "c" * 64,
    )
    attempt_payload = json.loads(attempt_path.read_text(encoding="utf-8"))

    assert receipt_payload["member_identity"] == expected
    assert attempt_payload["member_identity"] == expected
    assert receipt_path.name == f"{expected.removeprefix('sha256:')}.json"
    assert attempt_path.name == f"{expected.removeprefix('sha256:')}.json"


def test_pytest_member_identity_schema_change_has_one_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    member = "tests/test_version.py::test_identity"
    baseline = pytest_member_identity(member)
    monkeypatch.setattr(
        member_identity_owner,
        "PYTEST_MEMBER_IDENTITY_SCHEMA",
        "oh-goon.pytest-member/v999",
    )
    changed = pytest_member_identity(member)
    assert changed != baseline

    receipt_store = MemberReceiptStore(
        tmp_path / "receipts",
        suite_identity="sha256:" + "a" * 64,
    )
    receipt_path = receipt_store.record(member, outcome="PYTEST_MEMBER_PASSED")
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))

    execution_store = ExecutionMembershipStore(
        tmp_path / "execution",
        suite_identity="sha256:" + "a" * 64,
        execution_plan_identity="sha256:" + "b" * 64,
        run_identity="sha256:" + "c" * 64,
    )
    attempt_path = execution_store.record_started(
        member,
        attempt_identity="sha256:" + "d" * 64,
        observed_batch_identity="sha256:" + "e" * 64,
    )
    attempt_payload = json.loads(attempt_path.read_text(encoding="utf-8"))

    assert receipt_payload["member_identity"] == changed
    assert attempt_payload["member_identity"] == changed


def test_pytest_member_identity_contract_has_one_source_owner() -> None:
    root = (
        (Path(__file__).resolve().parents[2] / "backend")
        / "src"
        / "oh_goon"
        / "repository_tooling"
    )
    owner = root / "pytest_member_identity.py"
    consumers = (
        root / "test_batch_receipts.py",
        root / "pytest_execution_membership.py",
    )

    owner_tree = ast.parse(owner.read_text(encoding="utf-8"), filename=str(owner))
    owned_schema_values = {
        node.value
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("oh-goon.pytest-member/v")
    }
    assert len(owned_schema_values) == 1

    for consumer in consumers:
        source = consumer.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(consumer))
        assert "pytest_member_identity" in source
        assert not any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("oh-goon.pytest-member/v")
            for node in ast.walk(tree)
        )
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_member_identity"
            for node in ast.walk(tree)
        )
