from __future__ import annotations

import copy
import base64
import json
import shutil
import sys
import sysconfig
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from oh_goon.engine import command as command_owner
from oh_goon.integrations.hashmarks import development_verification as asset_owner
from oh_goon.integrations.hashmarks import runtime_contract
from oh_goon.repository_tooling import _dependency_lock_portability as lock_owner
from oh_goon.repository_tooling import verification_gate_control as gate
from oh_goon.repository_tooling.release import package_slim
from oh_goon.repository_tooling.verification_gate_control import (
    DOCUMENTATION_ASSET_OBLIGATION,
    REAL_LIFECYCLE_QA_PATHS,
    REAL_LIFECYCLE_QA_TEST,
    VERIFICATION_POLICY_TESTS,
    is_verification_policy_path,
    non_executable_documentation,
    required_static_obligations,
    validate_merge_proof,
)


_IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1XkAAAAASUVORK5CYII="
)


def _write_documentation_asset(root: Path, path: str) -> None:
    asset = root / path
    asset.parent.mkdir(parents=True)
    asset.write_bytes(_IMAGE_BYTES)
    (root / "docs/guide.md").write_text(
        f"![Overview]({Path(path).relative_to('docs')})\n", encoding="utf-8"
    )


def _asset_proof(root: Path, path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = asset_owner.nominate_changed_verification(
        root, (path,), asset_paths=(path,)
    )
    plan = {
        "schema_version": "oh-goon.development-verification/v1",
        "candidate": "a" * 40,
        "base": "b" * 40,
        "changed_paths": [path],
        "source_identity": package_slim.canonical_source_identity(root),
        "policy_status": "ORDINARY",
        "unverified": [],
        "behavioral_status": "NOT_APPLICABLE",
        "static": ["architecture"],
        "pytest": [],
        "frontend": [],
        "evidence": evidence,
        "obligations": [DOCUMENTATION_ASSET_OBLIGATION],
    }
    return plan, {
        "plan": copy.deepcopy(plan),
        "status": "MERGE_PROOF_READY",
        "outcomes": [{"owner": "Architecture", "returncode": 0}],
    }


def _validate_asset(root: Path, plan: dict[str, Any], result: dict[str, Any]) -> None:
    validate_merge_proof(
        plan,
        result,
        candidate=plan["candidate"],
        base=plan["base"],
        changed_paths=plan["changed_paths"],
        control_base=plan["base"],
        root=root,
    )


@pytest.mark.parametrize(
    "path", ("docs/assets/overview.png", "docs/assets/guide/figure.bin")
)
def test_native_asset_policy_is_independent_of_filename_and_suffix(
    tmp_path: Path, path: str
) -> None:
    _write_documentation_asset(tmp_path, path)
    plan, result = _asset_proof(tmp_path, path)

    _validate_asset(tmp_path, plan, result)

    asset = gate.admitted_documentation_assets((path,), plan["evidence"])[path]
    assert asset["content"]["media_type"] == "image/png"
    assert asset["content"]["integrity"] == "not-validated"
    assert asset["consumer_scope"]["completeness"] == "unknown"
    assert asset["consumer_scope"]["negative_evidence_admissible"] is False
    assert not non_executable_documentation(tmp_path, path)


@pytest.mark.parametrize(
    ("consumer", "content"),
    (
        (
            "backend/src/asset_reader.py",
            'from pathlib import Path\nblob = Path("docs/assets/overview.png").read_bytes()\n',
        ),
        (
            "tests/test_image.py",
            'from pathlib import Path\nblob = Path("docs/assets/overview.png").read_bytes()\n',
        ),
        ("Makefile", "publish:\n\tcp docs/assets/overview.png output.png\n"),
        ("docs/notes.md", "The overview.png file is mentioned without a link.\n"),
    ),
)
def test_observed_product_test_build_and_unqualified_uses_refuse_asset_policy(
    tmp_path: Path, consumer: str, content: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    destination = tmp_path / consumer
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    plan, result = _asset_proof(tmp_path, path)

    assert gate.admitted_documentation_assets((path,), plan["evidence"]) == {}
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        _validate_asset(tmp_path, plan, result)


@pytest.mark.parametrize(
    "content",
    (
        b"not an image",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:"
        + b"a" * 64
        + b"\nsize 123\n",
        _IMAGE_BYTES + b"x" * 1_048_576,
    ),
    ids=("text-with-image-suffix", "lfs-pointer", "producer-read-bound"),
)
def test_unobserved_image_bytes_cannot_receive_documentation_policy(
    tmp_path: Path, content: bytes
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    (tmp_path / path).write_bytes(content)
    plan, result = _asset_proof(tmp_path, path)

    assert gate.admitted_documentation_assets((path,), plan["evidence"]) == {}
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        _validate_asset(tmp_path, plan, result)


@pytest.mark.parametrize(
    "fault", ("stale", "source-gap", "truncated", "absent-member", "no-use")
)
def test_uncertain_or_stale_asset_observations_do_not_admit_policy(
    tmp_path: Path, fault: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    plan, _result = _asset_proof(tmp_path, path)
    packet = plan["evidence"]["asset_evidence"]
    asset = packet["changes"][0]["asset"]
    if fault == "stale":
        packet["freshness"] = "stale"
    elif fault == "source-gap":
        asset["consumer_scope"]["source_coverage"] = "incomplete"
    elif fault == "truncated":
        asset["consumer_scope"]["truncated"] = True
    elif fault == "absent-member":
        asset["supporting_source_members"][-1]["state"] = "known-absent"
    else:
        asset["consumers"] = []

    assert gate.admitted_documentation_assets((path,), plan["evidence"]) == {}


@pytest.mark.parametrize(
    "fault", ("content", "supporting-source", "missing-obligation")
)
def test_trusted_asset_observation_refuses_candidate_evidence_tampering(
    tmp_path: Path, fault: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    plan, result = _asset_proof(tmp_path, path)
    asset = plan["evidence"]["asset_evidence"]["changes"][0]["asset"]
    if fault == "content":
        asset["content"]["byte_count"] += 1
    elif fault == "supporting-source":
        asset["supporting_source_members"][-1]["member_revision"] = "f" * 64
    else:
        plan["obligations"] = []
    result["plan"] = copy.deepcopy(plan)

    with pytest.raises(
        ValueError, match="trusted source observation|explicitly admitted"
    ):
        _validate_asset(tmp_path, plan, result)


@pytest.mark.parametrize(
    "fault", ("image-replaced", "new-runtime-reader", "changed-document")
)
def test_trusted_asset_reobservation_detects_candidate_source_drift(
    tmp_path: Path, fault: str
) -> None:
    path = "docs/assets/overview.png"
    _write_documentation_asset(tmp_path, path)
    plan, result = _asset_proof(tmp_path, path)
    if fault == "image-replaced":
        (tmp_path / path).write_bytes(b"not image bytes")
    elif fault == "new-runtime-reader":
        reader = tmp_path / "backend/src/asset_reader.py"
        reader.parent.mkdir(parents=True)
        reader.write_text(
            'from pathlib import Path\nblob = Path("docs/assets/overview.png").read_bytes()\n',
            encoding="utf-8",
        )
    else:
        (tmp_path / "docs/guide.md").write_text("Image removed.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="trusted source observation"):
        _validate_asset(tmp_path, plan, result)


def _proof(
    *,
    source: str = "source.py",
    test_file: str = "tests/test_source.py",
    additional_tests: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = "sha256:" + "a" * 64
    test_files = [test_file, *additional_tests]
    nodes = [f"{path}::test_result" for path in test_files]
    plan = {
        "schema_version": "oh-goon.development-verification/v1",
        "candidate": "a" * 40,
        "base": "b" * 40,
        "changed_paths": [source, test_file],
        "source_identity": identity,
        "policy_status": "ORDINARY",
        "unverified": [],
        "behavioral_status": "REQUIRED",
        "static": ["python", "architecture"],
        "pytest": test_files,
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
                                "verification_target": {"path": test_file},
                            }
                        }
                    ],
                }
                for state, path in (
                    ("LINKED", source),
                    ("CHANGED_TEST", test_file),
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
                        "discovered": list(nodes),
                        "deselected": [],
                    },
                    "membership_receipt": {
                        "selected": list(nodes),
                        "passed": list(nodes),
                        "terminal_success": True,
                        "effective_pytest_outcomes": [
                            {
                                "member": node,
                                "status": "PASSED",
                                "attempt_identity": "attempt-1",
                                "pytest_outcomes": [
                                    {
                                        "nodeid": node,
                                        "outcome": "PASSED",
                                    }
                                ],
                            }
                            for node in nodes
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


def test_test_named_production_owner_qualifies_through_executed_consumer() -> None:
    plan, result = _proof(
        source="backend/src/oh_goon/repository_tooling/test_batch_receipts.py",
        test_file="scripts/tests/test_test_batch_receipts.py",
        additional_tests=VERIFICATION_POLICY_TESTS,
    )
    plan.update(
        policy_status="REVIEW_REQUIRED",
        obligations=["verification-policy protection"],
    )
    result["plan"] = copy.deepcopy(plan)

    def validate() -> None:
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=plan["base"],
        )

    validate()
    nomination = plan["evidence"]["observations"][0]["nominations"][0]["nomination"]
    nomination["verification_target"]["path"] = "scripts/tests/test_unexecuted.py"
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="no executed qualified consumer proof"):
        validate()


@pytest.mark.parametrize(
    ("changed_test", "state"),
    (
        ("backend/tests/unit/runtime/test_source.py", "LINKED"),
        ("backend/tests/integration/engine/test_source.py", "LINKED"),
        ("backend/tests/dependency_complete/test_source.py", "LINKED"),
        ("backend/tests/native/test_source.py", "LINKED"),
        ("scripts/tests/test_source.py", "LINKED"),
        ("tests/integration/test_source.py", "LINKED"),
        ("templates/goons/general/orders-taskflow/tests/test_source.py", "LINKED"),
        ("templates/goons/ml/iris-train/tests/test_source.py", "LINKED"),
        (
            "template-certification/fixtures/python/standard/tests/test_source.py",
            "LINKED",
        ),
        (
            "template-certification/fixtures/advanced/isolated-dataflow/tests/test_source.py",
            "LINKED",
        ),
        ("backend/tests/unit/runtime/source_test.py", "LINKED"),
        ("test/test_source.py", "LINKED"),
        ("verification/behavior_cases.py", "CHANGED_TEST"),
    ),
)
def test_changed_test_requires_own_execution_despite_linked_passes(
    changed_test: str, state: str
) -> None:
    plan, result = _proof(test_file=changed_test)
    plan["evidence"]["observations"][1]["state"] = state
    result["plan"] = copy.deepcopy(plan)

    def validate() -> None:
        validate_merge_proof(
            plan,
            result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=plan["changed_paths"],
            control_base=None,
        )

    validate()
    other_plan, result = _proof(test_file="tests/test_other.py")
    plan["pytest"] = other_plan["pytest"]
    for observation in plan["evidence"]["observations"]:
        observation["nominations"][0]["nomination"]["verification_target"]["path"] = (
            "tests/test_other.py"
        )
    result["plan"] = copy.deepcopy(plan)
    with pytest.raises(
        ValueError, match="Changed tests have no meaningful execution proof"
    ):
        validate()


@pytest.mark.parametrize("fault", ("omitted", "extra"))
def test_native_pytest_files_match_admitted_proof(fault: str) -> None:
    plan, result = _proof()
    _validate(plan, result)
    if fault == "omitted":
        plan["pytest"].append("tests/test_other.py")
        result["plan"] = copy.deepcopy(plan)
    else:
        native = result["outcomes"][0]["native_report"]
        node = "tests/test_other.py::test_other"
        native["native_collection"]["discovered"].append(node)
        receipt = native["membership_receipt"]
        receipt["selected"].append(node)
        receipt["passed"].append(node)
        receipt["effective_pytest_outcomes"].append(
            {
                "member": node,
                "status": "PASSED",
                "attempt_identity": "extra-attempt",
                "pytest_outcomes": [{"nodeid": node, "outcome": "PASSED"}],
            }
        )
    with pytest.raises(ValueError, match="Admitted pytest files"):
        _validate(plan, result)


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
    with pytest.raises(ValueError, match="Admitted pytest files"):
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
        "README.md",
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
        "backend/README.md",
        "backend/src/oh_goon/README.md",
        "scripts/README.md",
        "frontend/README.md",
        "frontend/src/README.md",
        "docs/development/REAL_LIFECYCLE_QA.md",
        "docs/development/REAL_LIFECYCLE_QA.yml",
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


@pytest.mark.parametrize(
    "path",
    (
        "README.md",
        "backend/README.md",
        "backend/src/oh_goon/README.md",
        "scripts/README.md",
        "frontend/README.md",
        "frontend/src/README.md",
        "docs/development/REAL_LIFECYCLE_QA.md",
        "docs/development/REAL_LIFECYCLE_QA.yml",
    ),
)
def test_governing_policy_admits_independently_bound_proof(path: str) -> None:
    plan, result = _proof()
    required = VERIFICATION_POLICY_TESTS + (
        (REAL_LIFECYCLE_QA_TEST,) if path in REAL_LIFECYCLE_QA_PATHS else ()
    )
    obligations = ["verification-policy protection"]
    if path in REAL_LIFECYCLE_QA_PATHS:
        obligations.append("real-lifecycle QA protection")
    plan.update(
        changed_paths=[path],
        policy_status="REVIEW_REQUIRED",
        obligations=obligations,
        pytest=list(required),
        static=sorted(required_static_obligations([path])),
    )
    plan["evidence"]["observations"] = [
        {"changed_path": path, "state": "UNAVAILABLE", "nominations": []}
    ]
    native = result["outcomes"][0]
    nodes = [f"{test_file}::test_policy" for test_file in required]
    native["native_report"]["native_collection"] = {
        "discovered": list(nodes),
        "deselected": [],
    }
    native["native_report"]["membership_receipt"] = {
        "selected": list(nodes),
        "passed": list(nodes),
        "terminal_success": True,
        "effective_pytest_outcomes": [
            {
                "member": node,
                "status": "PASSED",
                "attempt_identity": "policy-attempt",
                "pytest_outcomes": [{"nodeid": node, "outcome": "PASSED"}],
            }
            for node in nodes
        ],
    }
    owners = ["Architecture"]
    if path.startswith("backend/"):
        owners.extend(("Ruff", "Format", "Types"))
    if path.startswith("frontend/"):
        owners.extend(
            (
                "Frontend typecheck",
                "Frontend lint",
                "Frontend architecture",
                "Frontend i18n:check",
            )
        )
    result["outcomes"] = [native] + [
        {"owner": owner, "returncode": 0} for owner in owners
    ]
    result["plan"] = copy.deepcopy(plan)

    def validate(
        current_plan: dict[str, Any],
        current_result: dict[str, Any],
        control: str | None,
    ) -> None:
        validate_merge_proof(
            current_plan,
            current_result,
            candidate=plan["candidate"],
            base=plan["base"],
            changed_paths=[path],
            control_base=control,
        )

    validate(plan, result, plan["base"])
    with pytest.raises(ValueError, match="trusted-base"):
        validate(plan, result, None)

    for test_file in required:
        incomplete_plan, incomplete_result = copy.deepcopy((plan, result))
        incomplete_plan["pytest"].remove(test_file)
        incomplete_result["plan"] = copy.deepcopy(incomplete_plan)
        with pytest.raises(ValueError, match="policy protection was omitted"):
            validate(incomplete_plan, incomplete_result, plan["base"])

        incomplete_result = copy.deepcopy(result)
        report = incomplete_result["outcomes"][0]["native_report"]
        missing_node = f"{test_file}::test_policy"
        report["native_collection"]["discovered"].remove(missing_node)
        receipt = report["membership_receipt"]
        receipt["selected"].remove(missing_node)
        receipt["passed"].remove(missing_node)
        receipt["effective_pytest_outcomes"] = [
            row
            for row in receipt["effective_pytest_outcomes"]
            if row["member"] != missing_node
        ]
        with pytest.raises(ValueError, match="policy tests have no execution proof"):
            validate(plan, incomplete_result, plan["base"])

    if path in REAL_LIFECYCLE_QA_PATHS:
        incomplete_plan, incomplete_result = copy.deepcopy((plan, result))
        incomplete_plan["policy_status"] = "ORDINARY"
        incomplete_plan["obligations"].remove("real-lifecycle QA protection")
        incomplete_result["plan"] = copy.deepcopy(incomplete_plan)
        with pytest.raises(ValueError, match="policy protection was omitted"):
            validate(incomplete_plan, incomplete_result, plan["base"])

        incomplete_result = copy.deepcopy(result)
        receipt = incomplete_result["outcomes"][0]["native_report"][
            "membership_receipt"
        ]
        qa_outcome = next(
            row
            for row in receipt["effective_pytest_outcomes"]
            if row["member"] == f"{REAL_LIFECYCLE_QA_TEST}::test_policy"
        )
        qa_outcome["pytest_outcomes"][0]["outcome"] = "SKIPPED"
        with pytest.raises(ValueError, match="native category is skipped"):
            validate(plan, incomplete_result, plan["base"])

    extra_result = copy.deepcopy(result)
    report = extra_result["outcomes"][0]["native_report"]
    extra_file = (
        "scripts/tests/test_other_qa_contract.py"
        if path in REAL_LIFECYCLE_QA_PATHS
        else REAL_LIFECYCLE_QA_TEST
    )
    extra_node = f"{extra_file}::test_extra"
    report["native_collection"]["discovered"].append(extra_node)
    report["membership_receipt"]["selected"].append(extra_node)
    report["membership_receipt"]["passed"].append(extra_node)
    report["membership_receipt"]["effective_pytest_outcomes"].append(
        {
            "member": extra_node,
            "status": "PASSED",
            "attempt_identity": "extra-proof",
            "pytest_outcomes": [{"nodeid": extra_node, "outcome": "PASSED"}],
        }
    )
    with pytest.raises(ValueError, match="Admitted pytest files"):
        validate(plan, extra_result, plan["base"])


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


def _candidate_gate_root(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        '[project]\nname = "oh-goon"\nversion = "0.0.0"\n'
        '[build-system]\nrequires = []\nbuild-backend = "hatch_build"\n'
        'backend-path = ["."]\n',
        encoding="utf-8",
    )
    (backend / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "oh-goon"\nversion = "0.0.0"\n'
        'source = { editable = "." }\n[[package]]\nname = "hashmarks"\n'
        'version = "0.32.1"\nsource = { url = '
        '"https://github.com/Hugloss/Hashmarks/releases/download/v0.32.1/'
        'hashmarks-0.32.1-py3-none-any.whl" }\n',
        encoding="utf-8",
    )
    return root


def _candidate_distribution(environment: Path, source: str) -> tuple[Path, Path]:
    site = Path(
        sysconfig.get_path(
            "purelib", vars={"base": str(environment), "platbase": str(environment)}
        )
    )
    package = site / "hashmarks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (package / "implementation.py").write_text("VALUE = 1\n", encoding="utf-8")
    metadata = site / "hashmarks-0.32.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: hashmarks\nVersion: 0.32.1\n", encoding="utf-8"
    )
    (metadata / "RECORD").write_text(
        "hashmarks/__init__.py,,\nhashmarks/implementation.py,,\n"
        "hashmarks-0.32.1.dist-info/METADATA,,\n"
        "hashmarks-0.32.1.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    return package, metadata


def _stub_candidate_materialization(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: str = '__version__ = "0.32.1"\n',
    alter: Callable[[Path, Path], None] | None = None,
    result: command_owner.CommandResult | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []

    def run(*argv: str, **options: Any) -> command_owner.CommandResult:
        if argv[:3] == ("uv", "workspace", "dir"):
            return command_owner.CommandResult(
                0, argv[argv.index("--project") + 1] + "\n", ""
            )
        assert argv[:2] == ("uv", "sync")
        environment = Path(options["env"]["UV_PROJECT_ENVIRONMENT"])
        package, metadata = _candidate_distribution(environment, source)
        observations.append(
            {
                "argv": argv,
                "options": options,
                "environment": environment,
                "package": package,
                "identity": runtime_contract.native_producer_implementation_identity(
                    package
                ),
            }
        )
        if alter is not None:
            alter(package, metadata)
        return result or command_owner.CommandResult(0, "", "")

    monkeypatch.setattr(command_owner, "run_command", run)
    return observations


def test_candidate_producer_is_native_locked_data_without_candidate_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)
    marker = tmp_path / "candidate-executed"
    bomb = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "raise RuntimeError('candidate code must remain data')\n"
    )
    (root / "backend/hatch_build.py").write_text(bomb, encoding="utf-8")
    candidate_package = root / "backend/src/oh_goon"
    candidate_package.mkdir(parents=True)
    (candidate_package / "__init__.py").write_text(bomb, encoding="utf-8")
    for key, value in {
        "PYTHONPATH": str(root / "backend/src"),
        "PYTHONHOME": str(root),
        "VIRTUAL_ENV": str(root / "backend/.venv"),
        "UV_PROJECT_ENVIRONMENT": str(root / "backend/.venv"),
        "UV_PROJECT": str(root / "wrong-project"),
        "UV_PYTHON": str(root / "untrusted-python"),
        "UV_NO_BUILD": "false",
        "UV_NO_SYNC": "true",
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
    }.items():
        monkeypatch.setenv(key, value)
    observations = _stub_candidate_materialization(monkeypatch, source=bomb)

    identity = gate._candidate_producer_identity(root)

    assert len(observations) == 1
    observation = observations[0]
    assert identity == observation["identity"]
    assert identity != runtime_contract.require_loaded_hashmarks_runtime()
    argv = observation["argv"]
    assert argv[:2] == ("uv", "sync")
    assert {
        "--frozen",
        "--no-default-groups",
        "--no-install-project",
        "--no-install-workspace",
        "--no-build",
        "--no-config",
        "--no-python-downloads",
    } <= set(argv)
    assert argv[argv.index("--project") + 1] == str(root / "backend")
    assert argv[argv.index("--python") + 1] == sys.executable
    assert argv[argv.index("--link-mode") + 1] == "copy"
    environment = observation["environment"]
    assert not environment.is_relative_to(root)
    assert not environment.exists()
    child_environment = observation["options"]["env"]
    assert child_environment["UV_PROJECT_ENVIRONMENT"] == str(environment)
    assert child_environment["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "UV_PROJECT",
        "UV_PYTHON",
        "UV_NO_BUILD",
        "UV_NO_SYNC",
    ):
        assert key not in child_environment
    assert not marker.exists()
    assert not (root / "backend/.venv").exists()


@pytest.mark.process
def test_native_candidate_producer_probe_never_executes_candidate_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    root = tmp_path / "candidate"
    backend = root / "backend"
    backend.mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(checkout / "backend" / name, backend / name)
    original_lock = (backend / "uv.lock").read_bytes()
    marker = tmp_path / "candidate-executed"
    bomb = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "raise RuntimeError('candidate project code must remain data')\n"
    )
    (backend / "hatch_build.py").write_text(bomb, encoding="utf-8")
    candidate_package = backend / "src/oh_goon"
    candidate_package.mkdir(parents=True)
    (candidate_package / "__init__.py").write_text(bomb, encoding="utf-8")
    monkeypatch.setenv("UV_OFFLINE", "1")

    identity = gate._candidate_producer_identity(root)

    assert identity == runtime_contract.require_loaded_hashmarks_runtime()
    assert not marker.exists()
    assert (backend / "uv.lock").read_bytes() == original_lock
    assert not (backend / ".venv").exists()


@pytest.mark.process
def test_native_candidate_parent_workspace_cannot_select_another_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    root = tmp_path / "candidate"
    backend = root / "backend"
    backend.mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(checkout / "backend" / name, backend / name)
    original_lock = (backend / "uv.lock").read_bytes()
    (root / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["backend"]\n', encoding="utf-8"
    )
    monkeypatch.setenv("UV_OFFLINE", "1")
    native_run = command_owner.run_command
    commands: list[tuple[str, ...]] = []

    def observe(*argv: str, **options: Any) -> command_owner.CommandResult:
        commands.append(argv)
        return native_run(*argv, **options)

    monkeypatch.setattr(command_owner, "run_command", observe)

    with pytest.raises(ValueError, match="inspected backend lock"):
        gate._candidate_producer_identity(root)

    assert len(commands) == 1
    assert commands[0][:3] == ("uv", "workspace", "dir")
    assert (backend / "uv.lock").read_bytes() == original_lock
    assert not (root / "uv.lock").exists()
    assert not (backend / ".venv").exists()


@pytest.mark.parametrize("fault", ("missing", "mismatch", "truncated", "failure"))
def test_candidate_workspace_scope_must_be_complete_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    observations: list[dict[str, Any]] = []

    def workspace(*argv: str, **options: Any) -> command_owner.CommandResult:
        assert argv[:3] == ("uv", "workspace", "dir")
        observations.append({"argv": argv, "options": options})
        output = str(root / "backend") + "\n"
        if fault == "missing":
            output = ""
        elif fault == "mismatch":
            output = str(root) + "\n"
        return command_owner.CommandResult(
            17 if fault == "failure" else 0,
            output,
            "",
            stdout_truncated=fault == "truncated",
        )

    monkeypatch.setattr(command_owner, "run_command", workspace)

    with pytest.raises(ValueError, match="inspected backend lock"):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    argv = observations[0]["argv"]
    assert {"--no-config", "--offline", "--no-python-downloads"} <= set(argv)
    assert argv[argv.index("--project") + 1] == str(root / "backend")
    options = observations[0]["options"]
    assert not Path(options["env"]["UV_PROJECT_ENVIRONMENT"]).exists()
    assert not options["cwd"].exists()


def test_candidate_producer_identity_binds_bytes_even_when_version_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)
    first = _stub_candidate_materialization(
        monkeypatch, source='__version__ = "0.32.1"\nVALUE = "first"\n'
    )
    first_identity = gate._candidate_producer_identity(root)
    second = _stub_candidate_materialization(
        monkeypatch, source='__version__ = "0.32.1"\nVALUE = "second"\n'
    )
    second_identity = gate._candidate_producer_identity(root)

    assert first_identity == first[0]["identity"]
    assert second_identity == second[0]["identity"]
    assert first_identity != second_identity
    assert first[0]["environment"] != second[0]["environment"]


@pytest.mark.parametrize(
    "fault",
    (
        "missing",
        "duplicate",
        "missing-package",
        "package-symlink",
        "member-symlink",
        "metadata-symlink",
    ),
)
def test_candidate_producer_rejects_unbound_materialized_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)

    def alter(package: Path, metadata: Path) -> None:
        if fault == "missing":
            shutil.rmtree(metadata)
        elif fault == "duplicate":
            shutil.copytree(metadata, metadata.with_name("hashmarks-0.99.0.dist-info"))
        elif fault == "missing-package":
            shutil.rmtree(package)
        elif fault == "package-symlink":
            outside = tmp_path / "outside-package"
            package.rename(outside)
            package.symlink_to(outside, target_is_directory=True)
        elif fault == "member-symlink":
            outside = tmp_path / "outside-implementation.py"
            member = package / "implementation.py"
            member.rename(outside)
            member.symlink_to(outside)
        else:
            outside = tmp_path / "outside-metadata"
            metadata.rename(outside)
            metadata.symlink_to(outside, target_is_directory=True)

    observations = _stub_candidate_materialization(monkeypatch, alter=alter)

    with pytest.raises((OSError, ValueError)):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


@pytest.mark.parametrize("name", ("METADATA", "RECORD"))
@pytest.mark.parametrize("fault", ("missing", "directory", "symlink", "oversized"))
def test_candidate_distribution_metadata_is_safe_before_importlib_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)

    def alter(_package: Path, metadata: Path) -> None:
        path = metadata / name
        if fault == "oversized":
            with path.open("r+b") as stream:
                stream.truncate(8 * 1024 * 1024 + 1)
        elif fault == "symlink":
            outside = tmp_path / "outside-distribution-metadata"
            path.rename(outside)
            path.symlink_to(outside)
        else:
            path.unlink()
            if fault == "directory":
                path.mkdir()

    def unexpected_metadata_read(**_options: Any) -> None:
        pytest.fail("Unsafe distribution metadata reached importlib inspection")

    observations = _stub_candidate_materialization(monkeypatch, alter=alter)
    monkeypatch.setattr(metadata, "distributions", unexpected_metadata_read)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


def test_candidate_producer_rejects_non_native_egg_metadata_before_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)

    def alter(_package: Path, metadata: Path) -> None:
        legacy = metadata.with_name("hashmarks.egg-info")
        legacy.mkdir()
        shutil.copyfile(metadata / "METADATA", legacy / "PKG-INFO")

    def unexpected_metadata_read(**_options: Any) -> None:
        pytest.fail("Non-native distribution metadata reached importlib inspection")

    observations = _stub_candidate_materialization(monkeypatch, alter=alter)
    monkeypatch.setattr(metadata, "distributions", unexpected_metadata_read)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


@pytest.mark.parametrize("fault", ("failure", "truncated"))
def test_candidate_producer_never_falls_back_after_native_materialization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    result = command_owner.CommandResult(
        17 if fault == "failure" else 0,
        "partial native observation",
        "native failed" if fault == "failure" else "",
        stdout_truncated=fault == "truncated",
    )
    observations = _stub_candidate_materialization(monkeypatch, result=result)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert len(observations) == 1
    assert not observations[0]["environment"].exists()


def test_candidate_producer_timeout_is_terminal_and_removes_partial_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _candidate_gate_root(tmp_path)
    environments: list[Path] = []

    def timeout(*argv: str, **options: Any) -> command_owner.CommandResult:
        if argv[:3] == ("uv", "workspace", "dir"):
            return command_owner.CommandResult(0, str(root / "backend") + "\n", "")
        assert argv[:2] == ("uv", "sync")
        environment = Path(options["env"]["UV_PROJECT_ENVIRONMENT"])
        environment.mkdir(parents=True)
        (environment / "partial-install").write_text("incomplete", encoding="utf-8")
        environments.append(environment)
        raise command_owner.CommandTimeoutExpired(
            argv,
            options["timeout"],
            supervisor=command_owner.CommandSupervisorReceipt(),
        )

    monkeypatch.setattr(command_owner, "run_command", timeout)

    with pytest.raises(command_owner.CommandTimeoutExpired):
        gate._candidate_producer_identity(root)

    assert len(environments) == 1
    assert not environments[0].exists()


@pytest.mark.parametrize(
    "relative", ("backend", "backend/pyproject.toml", "backend/uv.lock")
)
def test_candidate_dependency_input_symlink_rejected_before_native_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    path = root / relative
    outside = tmp_path / "outside-input"
    directory = path.is_dir()
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=directory)
    observations = _stub_candidate_materialization(monkeypatch)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert observations == []


@pytest.mark.parametrize(
    "local_source",
    ('{ path = "../candidate.whl" }', '{ editable = "../hashmarks" }'),
)
def test_candidate_producer_rejects_local_dependency_before_native_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_source: str
) -> None:
    root = _candidate_gate_root(tmp_path)
    (root / "backend/uv.lock").write_text(
        'version = 1\n[[package]]\nname = "hashmarks"\nversion = "0.32.1"\n'
        f"source = {local_source}\n",
        encoding="utf-8",
    )
    observations = _stub_candidate_materialization(monkeypatch)

    with pytest.raises(ValueError):
        gate._candidate_producer_identity(root)

    assert observations == []


@pytest.mark.parametrize(
    "fault",
    (
        "none",
        "identity-mismatch",
        "invalid-plan",
        "unverified-plan",
        "native-timeout",
        "before-source-change",
        "after-source-change",
        "after-dirty-tree",
        "candidate-head-changed",
        "base-head-changed",
        "base-dirty-after",
        "foreign-asset-owner",
    ),
)
def test_merge_gate_binds_candidate_producer_and_rechecks_source_after_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
) -> None:
    root = _candidate_gate_root(tmp_path)
    trusted = tmp_path / "trusted"
    trusted_sources = trusted / "backend/src/oh_goon"
    trusted_sources.mkdir(parents=True)
    monkeypatch.setattr(
        gate,
        "__file__",
        str(trusted_sources / "repository_tooling/verification_gate_control.py"),
    )
    for module, relative in (
        (command_owner, "engine/command.py"),
        (asset_owner, "integrations/hashmarks/development_verification.py"),
        (runtime_contract, "integrations/hashmarks/runtime_contract.py"),
        (lock_owner, "repository_tooling/_dependency_lock_portability.py"),
        (package_slim, "repository_tooling/release/package_slim.py"),
    ):
        if module is asset_owner and fault == "foreign-asset-owner":
            continue
        monkeypatch.setattr(module, "__file__", str(trusted_sources / relative))
    plan, result = _proof()
    plan["requested_head"] = "c" * 40
    if fault == "invalid-plan":
        plan["schema_version"] = "unrecognized-plan/v1"
    elif fault == "unverified-plan":
        plan["unverified"] = ["changed source has unresolved scope"]
    result["plan"] = copy.deepcopy(plan)
    plan_file, result_file = tmp_path / "plan.json", tmp_path / "result.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    result_file.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oh-goon-verify-gate",
            "--plan",
            str(plan_file),
            "--result",
            str(result_file),
            "--candidate",
            plan["candidate"],
            "--base",
            plan["base"],
            "--control-base",
            plan["base"],
            "--head",
            plan["requested_head"],
            "--root",
            str(root),
            "--trusted-root",
            str(trusted),
        ],
    )
    observed: list[Path] = []

    def producer(candidate: Path) -> str:
        observed.append(candidate)
        if fault == "native-timeout":
            raise command_owner.CommandTimeoutExpired(
                ("uv", "sync"), 120, supervisor=command_owner.CommandSupervisorReceipt()
            )
        return "sha256:" + ("d" if fault == "identity-mismatch" else "b") * 64

    def source_identity(candidate: Path) -> str:
        assert candidate == root
        if fault == "before-source-change" or (
            fault == "after-source-change" and observed
        ):
            return "sha256:" + "e" * 64
        return str(plan["source_identity"])

    def git(*argv: str, **_options: Any) -> command_owner.CommandResult:
        assert argv[:2] == ("git", "-C")
        directory, arguments = Path(argv[2]), argv[3:]
        if arguments == ("rev-parse", "HEAD"):
            output = plan["base"] if directory == trusted else plan["candidate"]
            if observed and (
                (directory == root and fault == "candidate-head-changed")
                or (directory == trusted and fault == "base-head-changed")
            ):
                output = "f" * 40
        elif arguments[0] == "status":
            output = (
                "?? introduced-after-probe\n"
                if observed
                and (
                    (directory == root and fault == "after-dirty-tree")
                    or (directory == trusted and fault == "base-dirty-after")
                )
                else ""
            )
        elif arguments[0] == "merge-base":
            output = ""
        else:
            assert arguments[0] == "diff"
            output = "\0".join(plan["changed_paths"]) + "\0"
        return command_owner.CommandResult(0, output, "")

    monkeypatch.setattr(gate, "_candidate_producer_identity", producer)
    monkeypatch.setattr(package_slim, "canonical_source_identity", source_identity)
    monkeypatch.setattr(command_owner, "run_command", git)

    status = gate.main()

    assert status == (0 if fault == "none" else 1)
    assert observed == (
        []
        if fault
        in {
            "before-source-change",
            "invalid-plan",
            "unverified-plan",
            "foreign-asset-owner",
        }
        else [root]
    )
    output = capsys.readouterr().out
    assert ("MERGE_VERIFIED:" in output) == (fault == "none")
    assert ("MERGE UNVERIFIED:" in output) == (fault != "none")
