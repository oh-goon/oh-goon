from __future__ import annotations

import copy
from typing import Any

import pytest

from oh_goon.repository_tooling.verification_gate_control import (
    is_verification_policy_path,
    required_static_obligations,
    validate_merge_proof,
)


def _proof() -> tuple[dict[str, Any], dict[str, Any]]:
    identity = "sha256:" + "a" * 64
    plan = {
        "schema_version": "oh-goon.development-verification/v1",
        "candidate": "a" * 40,
        "base": "b" * 40,
        "changed_paths": ["source.py", "tests/test_source.py"],
        "source_identity": identity,
        "policy_status": "ORDINARY",
        "unverified": [],
        "behavioral_status": "REQUIRED",
        "static": ["python", "architecture"],
        "pytest": ["tests/test_source.py"],
        "frontend": [],
        "evidence": {
            "producer_implementation_identity": "sha256:" + "b" * 64,
            "owner_coverage": "not-claimed",
            "observations": [
                {
                    "state": state,
                    "changed_path": path,
                    "nominations": [
                        {
                            "nomination": {
                                "available": True,
                                "verification_target": {"path": "tests/test_source.py"},
                            }
                        }
                    ],
                }
                for state, path in (
                    ("LINKED", "source.py"),
                    ("CHANGED_TEST", "tests/test_source.py"),
                )
            ],
        },
    }
    result: dict[str, Any] = {
        "plan": copy.deepcopy(plan),
        "status": "MERGE_PROOF_READY",
        "outcomes": [
            {
                "owner": "Native pytest",
                "returncode": 0,
                "native_report": {
                    "status": "PASS",
                    "source_identity": identity,
                    "native_collection": {
                        "discovered": ["tests/test_source.py::test_result"],
                        "deselected": [],
                    },
                    "membership_receipt": {
                        "selected": ["tests/test_source.py::test_result"],
                        "passed": ["tests/test_source.py::test_result"],
                        "terminal_success": True,
                        "effective_pytest_outcomes": [
                            {
                                "member": "tests/test_source.py::test_result",
                                "status": "PASSED",
                                "attempt_identity": "attempt-1",
                                "pytest_outcomes": [
                                    {
                                        "nodeid": "tests/test_source.py::test_result",
                                        "outcome": "PASSED",
                                    }
                                ],
                            }
                        ],
                    },
                },
            }
        ],
    }
    result["outcomes"].extend(
        {"owner": name, "returncode": 0}
        for name in ("Ruff", "Format", "Types", "Architecture")
    )
    return plan, result


def _validate(
    plan: dict[str, Any], result: dict[str, Any], *, control: str | None = None
) -> None:
    validate_merge_proof(
        plan,
        result,
        candidate="a" * 40,
        base="b" * 40,
        changed_paths=["source.py", "tests/test_source.py"],
        control_base=control,
    )


def test_exact_fast_proof_does_not_claim_daily_full() -> None:
    _validate(*_proof())


@pytest.mark.parametrize(
    "fault",
    (
        "empty",
        "skip",
        "failure",
        "missing",
        "foreign-source",
        "foreign-sha",
        "omitted-test",
        "missing-owner",
        "cancelled",
    ),
)
def test_trusted_control_rejects_false_green(fault: str) -> None:
    plan, result = _proof()
    _validate(plan, result)
    native = result["outcomes"][0]["native_report"]
    if fault == "empty":
        native["membership_receipt"].update(selected=[], passed=[])
    elif fault == "skip":
        native["membership_receipt"]["effective_pytest_outcomes"][0]["pytest_outcomes"][
            0
        ]["outcome"] = "SKIPPED"
    elif fault == "failure":
        result["outcomes"][0]["returncode"] = 1
    elif fault == "missing":
        native["membership_receipt"]["missing"] = ["tests/test_source.py::test_result"]
    elif fault == "foreign-source":
        native["source_identity"] = "sha256:" + "c" * 64
    elif fault == "foreign-sha":
        plan["candidate"] = "c" * 40
        result["plan"] = copy.deepcopy(plan)
    elif fault == "omitted-test":
        native["membership_receipt"].update(
            selected=["tests/other.py::test_other"],
            passed=["tests/other.py::test_other"],
        )
    elif fault == "missing-owner":
        result["outcomes"] = []
    else:
        result["status"] = "CANCELLED"
    with pytest.raises(ValueError):
        _validate(plan, result)


def test_policy_change_cannot_approve_its_own_candidate_control() -> None:
    plan, result = _proof()
    plan["policy_status"] = "REVIEW_REQUIRED"
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="CUTOVER_UNVERIFIED"):
        _validate(plan, result)
    with pytest.raises(ValueError, match="trusted-base"):
        _validate(plan, result, control=plan["candidate"])
    _validate(plan, result, control=plan["base"])


def test_candidate_cannot_hide_policy_change_or_new_test_in_its_flags() -> None:
    plan, result = _proof()
    plan["changed_paths"] = [".github/workflows/ci.yml", "tests/test_source.py"]
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="trusted-base"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )
    plan, result = _proof()
    plan["evidence"]["observations"] = []
    result["plan"] = copy.deepcopy(plan)
    result["outcomes"][0]["native_report"]["membership_receipt"].update(
        selected=["tests/other.py::test_other"], passed=["tests/other.py::test_other"]
    )
    result["outcomes"][0]["native_report"]["native_collection"]["discovered"] = [
        "tests/other.py::test_other"
    ]
    effective = result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]
    effective["member"] = "tests/other.py::test_other"
    effective["pytest_outcomes"][0]["nodeid"] = "tests/other.py::test_other"
    with pytest.raises(ValueError, match="Changed tests"):
        _validate(plan, result)


def test_executable_change_cannot_claim_behavioral_na() -> None:
    plan, result = _proof()
    plan["behavioral_status"] = "NOT_APPLICABLE"
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="Executable inputs"):
        _validate(plan, result)


@pytest.mark.parametrize(
    "path",
    (
        "AGENTS.md",
        "docs/CI_CD_CONTRACT.md",
        "docs/development/TEST_QUALITY.md",
        "docs/development/CHATGPT_ITERATION.md",
        "scripts/development_check.sh",
        "backend/src/oh_goon/repository_tooling/verification_gate_control.py",
        "backend/src/oh_goon/repository_tooling/test_batches.py",
        "backend/src/oh_goon/repository_tooling/test_batch_receipts.py",
        "backend/src/oh_goon/repository_tooling/pytest_batch_receipts.py",
        ".github/workflows/ci.yml",
    ),
)
def test_policy_ingresses_cannot_self_approve_as_ordinary(path: str) -> None:
    plan, result = _proof()
    _validate(plan, result)
    assert is_verification_policy_path(path)
    plan["changed_paths"] = [path, "tests/test_source.py"]
    plan["static"] = sorted(required_static_obligations(plan["changed_paths"]))
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="trusted-base"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )


@pytest.mark.parametrize("value", ("node", {}, [True]))
def test_malformed_native_membership_cannot_qualify(value: Any) -> None:
    plan, result = _proof()
    _validate(plan, result)
    result["outcomes"][0]["native_report"]["membership_receipt"]["selected"] = value
    with pytest.raises(ValueError, match="shape"):
        _validate(plan, result)


@pytest.mark.parametrize(
    "category", ("FAILED", "SKIPPED", "XFAILED", "XPASSED", "UNREPORTED")
)
def test_effective_native_category_cannot_hide_behind_member_passed(
    category: str,
) -> None:
    plan, result = _proof()
    _validate(plan, result)
    result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]["pytest_outcomes"][0]["outcome"] = category
    with pytest.raises(ValueError, match="category"):
        _validate(plan, result)


def test_generated_only_contract_repair_requires_native_freshness_and_consumers() -> (
    None
):
    plan, _result = _proof()
    plan.update(
        changed_paths=[
            "generated/openapi/openapi.json",
            "generated/frontend/models.ts",
        ],
        static=["architecture", "generated", "frontend"],
        pytest=[],
        frontend=[],
        evidence={},
    )
    result: dict[str, Any] = {
        "plan": copy.deepcopy(plan),
        "status": "MERGE_PROOF_READY",
        "outcomes": [
            {"owner": name, "returncode": 0}
            for name in (
                "Architecture",
                "Generated contracts",
                "Frontend typecheck",
                "Frontend lint",
                "Frontend architecture",
                "Frontend i18n:check",
            )
        ],
    }
    validate_merge_proof(
        plan,
        result,
        candidate=plan["candidate"],
        base=plan["base"],
        changed_paths=plan["changed_paths"],
        control_base=None,
    )
    result["outcomes"][1]["returncode"] = 1
    with pytest.raises(ValueError, match="failed"):
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )


def test_historical_timeout_does_not_replace_the_original_effective_pass() -> None:
    plan, result = _proof()
    receipt = result["outcomes"][0]["native_report"]["membership_receipt"]
    receipt["attempt_records"] = [
        {
            "status": "STARTED",
            "pytest_outcomes": [
                {"nodeid": "tests/test_source.py::test_result", "outcome": "UNREPORTED"}
            ],
        }
    ]
    receipt["failed"] = ["tests/test_source.py::test_result"]
    receipt["non_passed"] = ["tests/test_source.py::test_result"]
    _validate(plan, result)


@pytest.mark.parametrize("nodeid", (None, "", 3))
def test_effective_native_case_requires_original_nonempty_identity(nodeid: Any) -> None:
    plan, result = _proof()
    _validate(plan, result)
    result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]["pytest_outcomes"][0]["nodeid"] = nodeid
    with pytest.raises(ValueError, match="category"):
        _validate(plan, result)


def test_effective_native_case_cannot_duplicate_an_occurrence() -> None:
    plan, result = _proof()
    _validate(plan, result)
    outcomes = result["outcomes"][0]["native_report"]["membership_receipt"][
        "effective_pytest_outcomes"
    ][0]["pytest_outcomes"]
    outcomes.append(copy.deepcopy(outcomes[0]))
    with pytest.raises(ValueError, match="category"):
        _validate(plan, result)


def test_legitimate_daily_deferral_is_not_failed_selected_membership() -> None:
    plan, result = _proof()
    collection = result["outcomes"][0]["native_report"]["native_collection"]
    collection["discovered"].append("tests/daily.py::test_full_corpus")
    collection["deselected"].append("tests/daily.py::test_full_corpus")
    _validate(plan, result)
