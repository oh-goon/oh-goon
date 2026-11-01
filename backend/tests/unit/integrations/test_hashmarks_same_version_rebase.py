from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("hashmarks")
from hashmarks import native_producer_implementation_identity as native_identity
from hashmarks.verification_selection import (
    VerificationSelectionEnvelopeState,
    downstream_consumption_contract,
    verification_member,
    verification_membership,
    verification_selection_envelope,
)

from oh_goon.integrations.hashmarks import runtime_contract
from oh_goon.integrations.hashmarks.verification_selection import (
    HashmarksVerificationSelectionInputError,
    load_hashmarks_verification_selection,
)
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
)

pytestmark = pytest.mark.dependency_complete

HISTORICAL_ARTIFACT_IDENTITY = (
    "sha256:c32cf232bfbb42eeffad7257eb215b0802bd0cbf8e35e8e4e8c2f0b4bc9eb5b3"
)
HISTORICAL_PRODUCER_IDENTITY = (
    "sha256:4cb5752192353474e19b7f6f30a81bd4da7a3d4e561eca781b01c84411cc3816"
)


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _write_selection(tmp_path: Path) -> tuple[Path, Path]:
    membership = verification_membership(
        [verification_member("tests/test_x.py", test_symbol="test_x")]
    )
    envelope = verification_selection_envelope(
        VerificationSelectionEnvelopeState(
            repository_identity="git-tree:same-version-rebase",
            source_identity=_sha("a"),
            codemap_generation=3,
            identity_generation=2,
            stale=False,
            membership=membership,
            owner_evidence={"selected": "src/x.py"},
            evidence_hashes={"verification_plan": _sha("b")},
        )
    )
    contract = downstream_consumption_contract(envelope)
    contract_path = tmp_path / "contract.json"
    envelope_path = tmp_path / "envelope.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    return contract_path, envelope_path


def _load(tmp_path: Path, artifact_identity: str = TEST_HASHMARKS_ARTIFACT_IDENTITY):
    contract_path, envelope_path = _write_selection(tmp_path)
    return load_hashmarks_verification_selection(
        contract_path,
        envelope_path,
        admitted_repository_identity="git-tree:same-version-rebase",
        admitted_source_identity=_sha("a"),
        expected_producer_artifact_identity=artifact_identity,
    )


def test_admitted_hashmarks_producer_and_artifact_are_jointly_admitted(tmp_path: Path) -> None:
    selection = _load(tmp_path)
    assert selection.producer_artifact_identity == TEST_HASHMARKS_ARTIFACT_IDENTITY
    assert (
        selection.producer_implementation_identity
        == TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY
    )


def test_artifact_provenance_cannot_replace_loaded_producer_authority(tmp_path: Path) -> None:
    selection = _load(tmp_path, HISTORICAL_ARTIFACT_IDENTITY)
    assert selection.producer_artifact_identity == HISTORICAL_ARTIFACT_IDENTITY
    assert (
        selection.producer_implementation_identity
        == TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY
    )


def test_historical_same_version_runtime_cannot_self_authorize_as_current_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        runtime_contract,
        "native_producer_implementation_identity",
        lambda: HISTORICAL_PRODUCER_IDENTITY,
    )
    with pytest.raises(
        HashmarksVerificationSelectionInputError,
        match="producer-implementation-identity-not-expected",
    ):
        _load(tmp_path)


def test_explicit_package_identity_observes_same_version_bytes_without_execution(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original" / "hashmarks"
    changed = tmp_path / "changed" / "hashmarks"
    initializer = (
        '__version__ = "fixture-version"\n'
        "from pathlib import Path\n"
        'Path(__file__).with_name("initializer-executed").write_text("unexpected")\n'
        'raise RuntimeError("artifact initializer must not execute")\n'
    )
    for package in (original, changed):
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(initializer, encoding="utf-8")
    implementation = original / "observer.py"
    implementation.write_text("def observe():\n    return 1\n", encoding="utf-8")
    (changed / "observer.py").write_text("def observe():\n    return 2\n", encoding="utf-8")

    before = runtime_contract.native_producer_implementation_identity(package_root=original)
    other = runtime_contract.native_producer_implementation_identity(package_root=changed)
    assert before == native_identity(package_root=original)
    assert other == native_identity(package_root=changed)
    assert before != other

    implementation.write_text("def observe():\n    return 2\n", encoding="utf-8")
    assert runtime_contract.native_producer_implementation_identity(package_root=original) == other
    for package in (original, changed):
        assert (package / "__init__.py").read_text(encoding="utf-8") == initializer
        assert not (package / "initializer-executed").exists()


def test_explicit_package_identity_does_not_authorize_the_loaded_runtime(tmp_path: Path) -> None:
    loaded = runtime_contract.require_loaded_hashmarks_runtime()
    package = tmp_path / "hashmarks"
    package.mkdir()
    (package / "__init__.py").write_text("__version__ = 'fixture-version'\n", encoding="utf-8")
    observed = runtime_contract.native_producer_implementation_identity(package_root=package)

    assert observed != loaded
    assert runtime_contract.native_producer_implementation_identity() == loaded
    assert (
        runtime_contract.require_loaded_hashmarks_runtime(expected_implementation_identity=loaded)
        == loaded
    )
    with pytest.raises(runtime_contract.HashmarksRuntimeContractError, match="does not match"):
        runtime_contract.require_loaded_hashmarks_runtime(expected_implementation_identity=observed)
