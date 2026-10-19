from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("hashmarks")
from hashmarks.verification_selection import (
    VerificationSelectionEnvelopeState,
    downstream_consumption_contract,
    verification_member,
    verification_membership,
    verification_selection_envelope,
)

from oh_goon.integrations.hashmarks.verification_selection import (
    HashmarksVerificationSelectionInputError,
    load_hashmarks_verification_selection,
)
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
)

pytestmark = pytest.mark.dependency_complete

_REPOSITORY = "git-tree:abc"
_SOURCE = "sha256:" + "1" * 64


def _native_payloads() -> tuple[dict[str, object], dict[str, object]]:
    membership = verification_membership(
        [
            verification_member("tests/test_a.py", test_symbol="test_a"),
            verification_member("tests/test_b.py"),
        ]
    )
    envelope = verification_selection_envelope(
        VerificationSelectionEnvelopeState(
            repository_identity=_REPOSITORY,
            source_identity=_SOURCE,
            codemap_generation=7,
            identity_generation=3,
            stale=False,
            membership=membership,
            owner_evidence={"selected": "src/a.py", "via": "import"},
            evidence_hashes={"ownership": "sha256:" + "2" * 64},
        )
    )
    return downstream_consumption_contract(envelope), envelope


def _load(
    tmp_path: Path,
    contract: dict[str, object],
    envelope: dict[str, object],
    *,
    repository: str = _REPOSITORY,
    source: str = _SOURCE,
    artifact: str = TEST_HASHMARKS_ARTIFACT_IDENTITY,
):
    contract_path = tmp_path / "contract.json"
    envelope_path = tmp_path / "envelope.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    return load_hashmarks_verification_selection(
        contract_path,
        envelope_path,
        admitted_repository_identity=repository,
        admitted_source_identity=source,
        expected_producer_artifact_identity=artifact,
    )


def test_native_hm160_contract_freezes_exact_executable_membership(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    verified = _load(tmp_path, contract, envelope)

    assert verified.producer_artifact_identity == TEST_HASHMARKS_ARTIFACT_IDENTITY
    assert verified.repository_identity == _REPOSITORY
    assert verified.source_identity == _SOURCE
    assert verified.membership_identity == contract["selected_membership_identity"]
    assert verified.repository_provenance_identity == contract["provenance_hash"]
    assert verified.codemap_generation == 7
    assert verified.identity_generation == 3
    assert verified.evidence_hashes == (("ownership", "sha256:" + "2" * 64),)
    assert verified.selected_test_nodes == ("tests/test_a.py::test_a", "tests/test_b.py")
    assert verified.unproven_owner_members == verified.selected_test_nodes


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_identity", "git-tree:wrong"),
        ("source_identity", "sha256:" + "9" * 64),
        ("hashmarks_identity", "hashmarks:0.11.20"),
        ("selected_membership_identity", "sha256:" + "8" * 64),
        ("provenance_hash", "sha256:" + "7" * 64),
        ("authority", "execution-authority"),
        ("execution_layout", "hashmarks-batches"),
    ],
)
def test_each_native_downstream_tamper_is_rejected_by_hashmarks_first(
    tmp_path: Path, field: str, value: str
) -> None:
    contract, envelope = _native_payloads()
    contract[field] = value
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="native consumer bundle is invalid"
    ):
        _load(tmp_path, contract, envelope)


def test_valid_native_bundle_repository_mismatch_is_rejected_by_oh_goon(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    with pytest.raises(HashmarksVerificationSelectionInputError, match="repository identity"):
        _load(tmp_path, contract, envelope, repository="git-tree:wrong")


def test_valid_native_bundle_source_mismatch_is_rejected_by_oh_goon(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    with pytest.raises(HashmarksVerificationSelectionInputError, match="source identity"):
        _load(tmp_path, contract, envelope, source="sha256:" + "9" * 64)


def test_artifact_identity_is_provenance_not_loaded_runtime_authority(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    observed = "sha256:" + "9" * 64
    verified = _load(tmp_path, contract, envelope, artifact=observed)
    assert verified.producer_artifact_identity == observed
    assert (
        verified.producer_implementation_identity == TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY
    )


def test_blank_artifact_provenance_is_rejected(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="artifact provenance identity"
    ):
        _load(tmp_path, contract, envelope, artifact="")


def test_missing_member_cannot_hide_behind_original_contract_digest(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    envelope["selection"]["members"].pop()  # type: ignore[index]
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="native consumer bundle is invalid"
    ):
        _load(tmp_path, contract, envelope)


def test_duplicate_member_fails_closed(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    members = envelope["selection"]["members"]  # type: ignore[index]
    members.append(deepcopy(members[0]))  # type: ignore[union-attr]
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="native consumer bundle is invalid"
    ):
        _load(tmp_path, contract, envelope)


def test_native_reordering_is_accepted_and_preserves_membership_identity(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    original_identity = contract["selected_membership_identity"]
    original_members = list(reversed(envelope["selection"]["members"]))  # type: ignore[index]
    membership = verification_membership(original_members)
    rebuilt = verification_selection_envelope(
        VerificationSelectionEnvelopeState(
            repository_identity=_REPOSITORY,
            source_identity=_SOURCE,
            codemap_generation=7,
            identity_generation=3,
            stale=False,
            membership=membership,
            owner_evidence={"selected": "src/a.py", "via": "import"},
            evidence_hashes={"ownership": "sha256:" + "2" * 64},
        )
    )
    rebuilt_contract = downstream_consumption_contract(rebuilt)
    verified = _load(tmp_path, rebuilt_contract, rebuilt)
    assert verified.membership_identity == original_identity


def test_member_id_tamper_is_rejected_by_native_hashmarks_before_oh_goon_admission(
    tmp_path: Path,
) -> None:
    _, envelope = _native_payloads()
    raw_member = envelope["selection"]["members"][0]  # type: ignore[index]
    raw_member["member_id"] = "sha256:" + "f" * 64  # type: ignore[index]
    with pytest.raises(ValueError, match="member-payload-identity-mismatch"):
        downstream_consumption_contract(envelope)


def test_producer_implementation_identity_is_cross_bound_to_retained_artifact(
    tmp_path: Path,
) -> None:
    contract, envelope = _native_payloads()
    verified = _load(tmp_path, contract, envelope)
    assert verified.producer_implementation_identity == contract["producer_implementation_identity"]
    assert (
        verified.producer_implementation_identity == envelope["producer"]["implementation_identity"]
    )  # type: ignore[index]


def test_same_version_different_producer_identity_fails_closed(tmp_path: Path) -> None:
    contract, envelope = _native_payloads()
    foreign = "sha256:" + "a" * 64
    envelope["producer"]["implementation_identity"] = foreign  # type: ignore[index]
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="native consumer bundle is invalid"
    ):
        _load(tmp_path, contract, envelope)


def test_stale_selection_fails_closed(tmp_path: Path) -> None:
    _, envelope = _native_payloads()
    membership = envelope["selection"]  # type: ignore[index]
    stale = verification_selection_envelope(
        VerificationSelectionEnvelopeState(
            repository_identity=_REPOSITORY,
            source_identity=_SOURCE,
            codemap_generation=7,
            identity_generation=3,
            stale=True,
            membership=membership,  # type: ignore[arg-type]
            owner_evidence={"selected": "src/a.py"},
            evidence_hashes={"ownership": "sha256:" + "2" * 64},
        )
    )
    with pytest.raises(HashmarksVerificationSelectionInputError, match="stale"):
        _load(tmp_path, downstream_consumption_contract(stale), stale)


def test_oh_goon_consumer_boundary_rejects_forbidden_authority_after_native_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, envelope = _native_payloads()
    contract["authority"] = "execution-authority"
    monkeypatch.setattr(
        "oh_goon.integrations.hashmarks.verification_selection_primitives.validate_native_consumer_bundle",
        lambda *_args, **_kwargs: {
            "valid": True,
            "producer_implementation_identity": envelope["producer"]["implementation_identity"],
            "execution_authority": "external",
        },
    )
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="repository intelligence only"
    ):
        _load(tmp_path, contract, envelope)


def test_oh_goon_consumer_boundary_rejects_producer_execution_layout_after_native_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, envelope = _native_payloads()
    contract["execution_layout"] = "producer-shards"
    monkeypatch.setattr(
        "oh_goon.integrations.hashmarks.verification_selection_primitives.validate_native_consumer_bundle",
        lambda *_args, **_kwargs: {
            "valid": True,
            "producer_implementation_identity": envelope["producer"]["implementation_identity"],
            "execution_authority": "external",
        },
    )
    with pytest.raises(HashmarksVerificationSelectionInputError, match="physical execution layout"):
        _load(tmp_path, contract, envelope)


def test_oh_goon_consumer_boundary_rejects_nonexternal_execution_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, envelope = _native_payloads()
    monkeypatch.setattr(
        "oh_goon.integrations.hashmarks.verification_selection_primitives.validate_native_consumer_bundle",
        lambda *_args, **_kwargs: {
            "valid": True,
            "producer_implementation_identity": envelope["producer"]["implementation_identity"],
            "execution_authority": "producer",
        },
    )
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="execution authority external"
    ):
        _load(tmp_path, contract, envelope)


@pytest.mark.parametrize(
    "field",
    [
        "timeout",
        "workers",
        "retry_count",
        "execute_together",
        "execution_order",
        "machine",
        "preferred_granularity",
        "may_regroup",
        "owner_units",
        "isolated_process",
        "worker_hint",
        "physical_shards",
    ],
)
def test_future_hashmarks_execution_policy_fields_cannot_gain_authority(
    tmp_path: Path, field: str
) -> None:
    contract, envelope = _native_payloads()
    contract[field] = 900 if field == "timeout" else "attacker-controlled"
    with pytest.raises(
        HashmarksVerificationSelectionInputError, match="native consumer bundle is invalid"
    ):
        _load(tmp_path, contract, envelope)
