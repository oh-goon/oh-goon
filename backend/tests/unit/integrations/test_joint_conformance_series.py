from __future__ import annotations

import pytest

pytest.importorskip("hashmarks")

import json
from pathlib import Path

from hashmarks.verification_selection import verification_member, verification_membership

from oh_goon.integrations.hashmarks.verification_selection import (
    HashmarksVerificationMember,
    VerifiedHashmarksVerificationSelection,
)
from oh_goon.repository_tooling.hashmarks_verification_execution import (
    VerificationExecutionGroupingPolicy,
    build_verification_execution_plan,
)
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
    TEST_HASHMARKS_VERSION,
)

pytestmark = pytest.mark.dependency_complete

ROOT = Path(__file__).resolve().parents[4]
HISTORICAL_SERIES = ROOT / "docs" / "certification" / "joint-conformance-series-188.json"
PREVIOUS_CANONICAL_SERIES = ROOT / "docs" / "certification" / "joint-conformance-series-210.json"
PREVIOUS_HM160_SERIES = ROOT / "docs" / "certification" / "joint-conformance-series-hm160.json"
PREVIOUS_V013_SERIES = ROOT / "docs" / "certification" / "joint-conformance-series-v0.13.0.json"
PREVIOUS_HM260_SERIES = ROOT / "docs" / "certification" / "joint-conformance-series-hm260.json"
PREVIOUS_HM282_SERIES = ROOT / "docs" / "certification" / "joint-conformance-series-hm282.json"
PREVIOUS_HM292_SERIES = ROOT / "docs" / "certification" / "joint-conformance-series-hm292.json"
HISTORICAL_ARTIFACT_IDENTITY = (
    "sha256:2e8b81e82eb1642db06ad8f9efecc7acbffe975e483d8e9f1f7fd6a7530b504c"
)
HISTORICAL_PRODUCER_IDENTITY = (
    "sha256:54e26953c0a01588328333ad0d7296ffb3b8aefcb3a287785b27859866a573fb"
)


def _selection(count: int = 7) -> VerifiedHashmarksVerificationSelection:
    native = verification_membership(
        [
            verification_member("tests/test_x.py", test_symbol=f"test_{index}")
            for index in range(count)
        ]
    )
    members = tuple(
        HashmarksVerificationMember(
            path=str(row["path"]),
            test_symbol=str(row["test_symbol"]) if row["test_symbol"] is not None else None,
            member_id=str(row["member_id"]),
        )
        for row in native["members"]
    )
    return VerifiedHashmarksVerificationSelection(
        producer_version=TEST_HASHMARKS_VERSION,
        producer_artifact_identity=TEST_HASHMARKS_ARTIFACT_IDENTITY,
        producer_implementation_identity=TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
        repository_identity="git-tree:joint-series",
        source_identity="sha256:" + "d" * 64,
        repository_provenance_identity="sha256:" + "e" * 64,
        membership_identity=str(native["membership_identity"]),
        producer_contract_identity="sha256:" + "f" * 64,
        codemap_generation=1,
        identity_generation=1,
        evidence_hashes=(),
        members=members,
    )


def _load(path: Path = HISTORICAL_SERIES) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_joint_series_freezes_exact_parent_and_hashmarks_producer_identity() -> None:
    series = _load()
    assert series["schema"] == "oh-goon.hashmarks-joint-conformance-series.v1"
    assert series["series_id"] == "joint-188-au-v1"
    assert series["oh_goon_parent"]["version"] == "1267.0.188"  # type: ignore[index]
    assert (
        series["oh_goon_parent"]["zip_sha256"]
        == "968719894ac1dd142522c4e3bd2791d071d97a4b37a4f05b0f0d64347e7433fe"
    )  # type: ignore[index]
    assert (
        series["oh_goon_parent"]["source_identity"]
        == "sha256:08d0d02814a6b2a7b35a244e0ac571d34b6c348366f958c6ed053ccb4041a1ea"
    )  # type: ignore[index]
    assert series["hashmarks"]["artifact_identity"] == HISTORICAL_ARTIFACT_IDENTITY  # type: ignore[index]
    assert series["hashmarks"]["producer_implementation_identity"] == HISTORICAL_PRODUCER_IDENTITY  # type: ignore[index]


def test_joint_series_keeps_authority_ownership_explicit() -> None:
    authority = _load()["authority"]
    assert authority == {
        "hashmarks": "repository-intelligence-and-verification-selection",
        "oh_goon": "admission-execution-verification-certification",
        "consumer_rule": "verify-producer-semantics-never-recreate-repair-or-reinterpret",
    }


def test_joint_series_records_only_exactly_qualified_experiments() -> None:
    experiments = _load()["experiments"]
    assert set(experiments) == {f"X{index:02d}" for index in range(1, 17)}
    assert all(
        value in {"covered", "covered-by-prior-phase", "native-reproof-required"}
        for value in experiments.values()
    )
    assert experiments["X13"] == "covered-by-prior-phase"
    assert experiments["X14"] == "covered-by-prior-phase"
    assert experiments["X16"] == "native-reproof-required"


def test_physical_layout_change_cannot_change_hashmarks_membership_identity() -> None:
    selection = _selection(7)
    one = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    four = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(4),
    )
    assert one.plan_identity != four.plan_identity
    assert (
        one.selected_membership_identity
        == four.selected_membership_identity
        == selection.membership_identity
    )
    assert (
        tuple(member for shard in one.shards for member in shard.members)
        == selection.selected_test_nodes
    )
    assert (
        tuple(member for shard in four.shards for member in shard.members)
        == selection.selected_test_nodes
    )


def test_previous_canonical_joint_series_remains_immutable() -> None:
    series = _load(PREVIOUS_CANONICAL_SERIES)
    assert series["series_id"] == "joint-210-canonical-hashmarks-v1"
    assert series["oh_goon_parent"]["version"] == "1267.0.209"  # type: ignore[index]


def test_previous_hm160_joint_series_remains_immutable() -> None:
    series = _load(PREVIOUS_HM160_SERIES)
    assert series["series_id"] == "joint-750-hm160-v1"


def test_previous_v013_joint_series_remains_immutable() -> None:
    series = _load(PREVIOUS_V013_SERIES)
    assert series["series_id"] == "joint-839-hashmarks-v0.13.0-v1"
    assert series["oh_goon_parent"]["version"] == "1267.0.838"  # type: ignore[index]
    assert series["hashmarks"]["canonical_source_archive_identity"] == (  # type: ignore[index]
        "sha256:168bf08149ea3fd64d708c03d79317415f97a7abbb9558d6c4b33afed04418fb"
    )


def test_previous_hm260_joint_series_remains_immutable() -> None:
    series = _load(PREVIOUS_HM260_SERIES)
    assert series["series_id"] == "joint-860-hashmarks-hm260-v1"
    assert series["hashmarks"]["checkpoint"] == "HM260"  # type: ignore[index]


def test_previous_hm282_joint_series_remains_immutable() -> None:
    series = _load(PREVIOUS_HM282_SERIES)
    assert series["series_id"] == "joint-875-hashmarks-hm282-v1"
    assert series["hashmarks"]["checkpoint"] == "HM282"  # type: ignore[index]


def test_previous_hm292_joint_series_remains_immutable() -> None:
    series = _load(PREVIOUS_HM292_SERIES)
    assert series["series_id"] == "joint-885-hashmarks-hm292-v1"
    assert series["hashmarks"]["checkpoint"] == "HM292"  # type: ignore[index]


def test_runtime_contract_rejects_historical_producer_authority() -> None:
    assert TEST_HASHMARKS_ARTIFACT_IDENTITY != HISTORICAL_ARTIFACT_IDENTITY
    assert TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY != HISTORICAL_PRODUCER_IDENTITY
