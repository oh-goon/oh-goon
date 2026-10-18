from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from oh_goon.repository_tooling import recovery_checkpoint as subject


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    return root


def _fake_packager(monkeypatch, identity: str) -> None:
    monkeypatch.setattr(subject, "canonical_source_identity", lambda _root: identity)

    def package(output: Path, *, root: Path) -> str:
        del root
        output.write_bytes(b"archive")
        return hashlib.sha256(b"archive").hexdigest()

    monkeypatch.setattr(subject, "package_slim", package)
    monkeypatch.setattr(subject, "archive_source_identity", lambda _archive: identity)


def _continuation_input(
    *, qualification_state: str = "NATIVE-QUALIFICATION-INCOMPLETE", next_phase: str = "phase-2"
) -> bytes:
    open_phases = [{"id": next_phase, "state": "OPEN"}] if next_phase else []
    payload = {
        "schema": subject.CONTINUATION_INPUT_SCHEMA,
        "completed_phases": [{"id": "phase-1", "state": "NEWLY_CLOSED"}],
        "open_phases": open_phases,
        "proof_receipts": [{"selection": "focused", "outcome": "PASS"}],
        "qualification_state": qualification_state,
        "next_executable_phase": next_phase or None,
        "durability_invariant": "session/container state is disposable",
    }
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def _checkpoint(tmp_path: Path, monkeypatch, identity: str = "sha256:" + "a" * 64):
    root = _source(tmp_path)
    publisher = subject.FilesystemRecoveryPublisher(tmp_path / "durable")
    _fake_packager(monkeypatch, identity)
    head = subject.checkpoint(
        root=root,
        publisher=publisher,
        campaign_id="campaign",
        expected_generation=0,
        expected_head_sha256=None,
        continuation_input=_continuation_input(),
        proof_level="NONE",
        qualification_state="NATIVE-QUALIFICATION-INCOMPLETE",
    )
    return root, publisher, head


def test_external_recovery_path_accepts_transport_outside_source(tmp_path: Path) -> None:
    root = _source(tmp_path)
    external = tmp_path / "durable" / "continuation-input.json"
    assert (
        subject._require_external_recovery_path(
            source_root=root, candidate=external, label="recovery continuation input"
        )
        == external.resolve()
    )


def test_checkpoint_cli_rejects_store_inside_source_before_creating_it(tmp_path: Path) -> None:
    root = _source(tmp_path)
    continuation = tmp_path / "continuation-input.json"
    continuation.write_bytes(_continuation_input())
    store = root / "recovery-store"

    with pytest.raises(RuntimeError, match="recovery store must be outside canonical source root"):
        subject.main(
            [
                "checkpoint",
                "--root",
                str(root),
                "--store",
                str(store),
                "--campaign-id",
                "campaign",
                "--expected-generation",
                "0",
                "--continuation-input",
                str(continuation),
            ]
        )

    assert not store.exists()


def test_checkpoint_cli_rejects_continuation_input_inside_source_before_store_creation(
    tmp_path: Path,
) -> None:
    root = _source(tmp_path)
    continuation = root / "continuation-input.json"
    continuation.write_bytes(_continuation_input())
    store = tmp_path / "durable"

    with pytest.raises(
        RuntimeError, match="recovery continuation input must be outside canonical source root"
    ):
        subject.main(
            [
                "checkpoint",
                "--root",
                str(root),
                "--store",
                str(store),
                "--campaign-id",
                "campaign",
                "--expected-generation",
                "0",
                "--continuation-input",
                str(continuation),
            ]
        )

    assert not store.exists()


def test_verify_cli_rejects_store_inside_subject_before_creating_it(tmp_path: Path) -> None:
    root = _source(tmp_path)
    store = root / "recovery-store"

    with pytest.raises(RuntimeError, match="recovery store must be outside canonical source root"):
        subject.main(
            [
                "verify",
                "--store",
                str(store),
                "--subject-root",
                str(root),
            ]
        )

    assert not store.exists()


def test_resume_cli_rejects_destination_inside_subject_before_store_access(tmp_path: Path) -> None:
    root = _source(tmp_path)
    store = tmp_path / "durable"
    destination = root / "restored"

    with pytest.raises(
        RuntimeError, match="recovery destination must be outside canonical source root"
    ):
        subject.main(
            [
                "resume",
                "--store",
                str(store),
                "--destination",
                str(destination),
                "--subject-root",
                str(root),
            ]
        )

    assert not store.exists()
    assert not destination.exists()


def test_checkpoint_publishes_content_addressed_bytes_before_cas_head(
    tmp_path: Path, monkeypatch
) -> None:
    _root, publisher, head = _checkpoint(tmp_path, monkeypatch)
    receipt = subject._verify_receipt(publisher, head.checkpoint_receipt_sha256)
    assert receipt.object_key == f"objects/sha256/{receipt.archive_sha256}"
    assert publisher.read(receipt.object_key) == b"archive"
    assert subject.verify(publisher=publisher) == head


def test_stale_writer_requires_generation_and_exact_head_identity(
    tmp_path: Path, monkeypatch
) -> None:
    root, publisher, head = _checkpoint(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="generation changed"):
        subject.checkpoint(
            root=root,
            publisher=publisher,
            campaign_id="campaign",
            expected_generation=0,
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            expected_head_sha256=None,
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )
    with pytest.raises(RuntimeError, match="identity changed"):
        subject.checkpoint(
            root=root,
            publisher=publisher,
            campaign_id="campaign",
            expected_generation=1,
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            expected_head_sha256="bad",
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )
    current = publisher.read("RECOVERY_HEAD.json")
    assert hashlib.sha256(current).hexdigest() != "bad"
    assert subject._head_from_bytes(current) == head


def test_conflicting_immutable_object_fails_closed(tmp_path: Path) -> None:
    publisher = subject.FilesystemRecoveryPublisher(tmp_path / "durable")
    publisher.publish_immutable("objects/sha256/key", b"one")
    with pytest.raises(RuntimeError, match="conflicts"):
        publisher.publish_immutable("objects/sha256/key", b"two")


def test_publisher_without_conditional_head_is_rejected(tmp_path: Path, monkeypatch) -> None:
    root = _source(tmp_path)
    _fake_packager(monkeypatch, "sha256:" + "b" * 64)

    class Unsafe(subject.FilesystemRecoveryPublisher):
        supports_conditional_head = False

    with pytest.raises(RuntimeError, match="lacks safe conditional"):
        subject.checkpoint(
            root=root,
            publisher=Unsafe(tmp_path / "durable"),
            campaign_id="campaign",
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            expected_generation=0,
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )


def test_structured_proof_must_bind_exact_result_source(tmp_path: Path, monkeypatch) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "c" * 64
    _fake_packager(monkeypatch, identity)
    publisher = subject.FilesystemRecoveryPublisher(tmp_path / "durable")
    wrong = subject.create_proof_receipt(
        source_identity="sha256:" + "d" * 64,
        test_command_identity="cmd",
        test_selection="focused",
        environment_identity="env",
        evidence_identity="evidence",
        timestamp="2026-09-25T00:00:00Z",
        proof_level="FOCUSED_PROVEN",
    )
    with pytest.raises(RuntimeError, match="does not bind"):
        subject.checkpoint(
            root=root,
            publisher=publisher,
            campaign_id="campaign",
            expected_generation=0,
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            proof_level="FOCUSED_PROVEN",
            qualification_state="INCOMPLETE",
            proof_receipt=wrong,
        )
    assert not (tmp_path / "durable" / "RECOVERY_HEAD.json").exists()


def test_structured_proof_survives_durable_readback(tmp_path: Path, monkeypatch) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "e" * 64
    _fake_packager(monkeypatch, identity)
    publisher = subject.FilesystemRecoveryPublisher(tmp_path / "durable")
    proof = subject.create_proof_receipt(
        source_identity=identity,
        test_command_identity="cmd",
        test_selection="focused",
        environment_identity="env",
        evidence_identity="evidence",
        timestamp="2026-09-25T00:00:00Z",
        proof_level="FOCUSED_PROVEN",
    )
    head = subject.checkpoint(
        root=root,
        publisher=publisher,
        campaign_id="campaign",
        expected_generation=0,
        continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
        proof_level="FOCUSED_PROVEN",
        qualification_state="INCOMPLETE",
        proof_receipt=proof,
    )
    assert head.proof_level == "FOCUSED_PROVEN"
    assert subject.verify(publisher=publisher) == head


@pytest.mark.parametrize(
    "boundary",
    ["archive_publish", "archive_verify", "continuation_publish", "receipt_publish", "before_cas"],
)
def test_crash_before_cas_never_creates_intermediate_authority(
    tmp_path: Path, monkeypatch, boundary: str
) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "f" * 64
    _fake_packager(monkeypatch, identity)

    class Crashing(subject.FilesystemRecoveryPublisher):
        def publish_immutable(self, key: str, content: bytes) -> None:
            super().publish_immutable(key, content)
            if boundary == "archive_publish" and key.startswith("objects/"):
                raise RuntimeError("injected crash")
            if boundary == "continuation_publish" and key.startswith("continuations/"):
                raise RuntimeError("injected crash")

        def stat(self, key: str):
            value = super().stat(key)
            if boundary == "archive_verify" and key.startswith("objects/"):
                raise RuntimeError("injected crash")
            return value

        def publish_receipt(self, key: str, content: bytes) -> None:
            super().publish_receipt(key, content)
            if boundary == "receipt_publish" and key.startswith("receipts/"):
                raise RuntimeError("injected crash")

        def compare_and_swap_head(self, **kwargs) -> bool:
            if boundary == "before_cas":
                raise RuntimeError("injected crash")
            return super().compare_and_swap_head(**kwargs)

    publisher = Crashing(tmp_path / "durable")
    with pytest.raises(RuntimeError, match="injected crash"):
        subject.checkpoint(
            root=root,
            publisher=publisher,
            campaign_id="campaign",
            expected_generation=0,
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )
    assert not (tmp_path / "durable" / "RECOVERY_HEAD.json").exists()


def test_crash_after_successful_cas_leaves_new_head_fully_recoverable(
    tmp_path: Path, monkeypatch
) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "1" * 64
    _fake_packager(monkeypatch, identity)

    class CrashAfterCas(subject.FilesystemRecoveryPublisher):
        def compare_and_swap_head(self, **kwargs) -> bool:
            assert super().compare_and_swap_head(**kwargs)
            raise RuntimeError("injected crash after cas")

    store = tmp_path / "durable"
    publisher = CrashAfterCas(store)
    with pytest.raises(RuntimeError, match="after cas"):
        subject.checkpoint(
            root=root,
            publisher=publisher,
            campaign_id="campaign",
            expected_generation=0,
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )
    stable = subject.FilesystemRecoveryPublisher(store)
    head = subject.verify(publisher=stable)
    assert head.generation == 1


def test_resume_reconstructs_committed_source_into_empty_destination(
    tmp_path: Path, monkeypatch
) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "2" * 64
    publisher = subject.FilesystemRecoveryPublisher(tmp_path / "durable")
    monkeypatch.setattr(subject, "canonical_source_identity", lambda _root: identity)

    def package(output: Path, *, root: Path) -> str:
        import zipfile

        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("source/VERSION", (root / "VERSION").read_bytes())
        return hashlib.sha256(output.read_bytes()).hexdigest()

    monkeypatch.setattr(subject, "package_slim", package)
    monkeypatch.setattr(subject, "archive_source_identity", lambda _archive: identity)
    head = subject.checkpoint(
        root=root,
        publisher=publisher,
        campaign_id="campaign",
        expected_generation=0,
        continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
        proof_level="NONE",
        qualification_state="INCOMPLETE",
    )
    destination = tmp_path / "restored"
    assert subject.resume(publisher=publisher, destination=destination) == head
    assert (destination / "source" / "VERSION").read_text() == "1.2.3\n"


def test_retry_after_receipt_publish_resumes_at_cas_without_rebuild(
    tmp_path: Path, monkeypatch
) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "3" * 64
    calls = {"package": 0}
    monkeypatch.setattr(subject, "canonical_source_identity", lambda _root: identity)

    def package(output: Path, *, root: Path) -> str:
        del root
        calls["package"] += 1
        output.write_bytes(b"archive")
        return hashlib.sha256(b"archive").hexdigest()

    monkeypatch.setattr(subject, "package_slim", package)
    monkeypatch.setattr(subject, "archive_source_identity", lambda _archive: identity)

    class CrashBeforeCas(subject.FilesystemRecoveryPublisher):
        def compare_and_swap_head(self, **kwargs) -> bool:
            raise RuntimeError("crash before cas")

    store = tmp_path / "durable"
    with pytest.raises(RuntimeError, match="before cas"):
        subject.checkpoint(
            root=root,
            publisher=CrashBeforeCas(store),
            campaign_id="campaign",
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            expected_generation=0,
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )
    assert calls["package"] == 1
    head = subject.checkpoint(
        root=root,
        publisher=subject.FilesystemRecoveryPublisher(store),
        campaign_id="campaign",
        continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
        expected_generation=0,
        proof_level="NONE",
        qualification_state="INCOMPLETE",
    )
    assert calls["package"] == 1
    assert head.generation == 1


@pytest.mark.parametrize(
    "state",
    [
        subject.PublicationState.BUILT,
        subject.PublicationState.LOCALLY_VERIFIED,
        subject.PublicationState.PUBLISHED,
        subject.PublicationState.DURABLY_VERIFIED,
        subject.PublicationState.RECEIPTED,
    ],
)
def test_state_boundary_crashes_before_commit_leave_old_head_authoritative(
    tmp_path: Path, monkeypatch, state: subject.PublicationState
) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "4" * 64
    _fake_packager(monkeypatch, identity)
    publisher = subject.FilesystemRecoveryPublisher(tmp_path / "durable")

    def crash(observed: subject.PublicationState) -> None:
        if observed == state:
            raise RuntimeError(f"crash at {state}")

    with pytest.raises(RuntimeError, match="crash at"):
        subject.checkpoint(
            root=root,
            publisher=publisher,
            campaign_id="campaign",
            expected_generation=0,
            continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
            proof_level="NONE",
            qualification_state="INCOMPLETE",
            state_hook=crash,
        )
    assert not (tmp_path / "durable" / "RECOVERY_HEAD.json").exists()


def test_publication_states_reach_committed_only_after_head_cas(
    tmp_path: Path, monkeypatch
) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "5" * 64
    _fake_packager(monkeypatch, identity)
    states: list[subject.PublicationState] = []
    subject.checkpoint(
        root=root,
        publisher=subject.FilesystemRecoveryPublisher(tmp_path / "durable"),
        continuation_input=_continuation_input(qualification_state="INCOMPLETE"),
        campaign_id="campaign",
        expected_generation=0,
        proof_level="NONE",
        qualification_state="INCOMPLETE",
        state_hook=states.append,
    )
    assert states == list(subject.PublicationState)


def test_checkpoint_binds_continuation_state_to_receipt_and_head(
    tmp_path: Path, monkeypatch
) -> None:
    _root, publisher, head = _checkpoint(tmp_path, monkeypatch)
    receipt = subject._verify_receipt(publisher, head.checkpoint_receipt_sha256)
    assert head.continuation_state_sha256 == receipt.continuation_state_sha256
    assert receipt.continuation_object_key == (
        f"continuations/sha256/{receipt.continuation_state_sha256}"
    )
    status = subject.status(publisher=publisher)
    continuation = status["continuation"]
    assert continuation["current_authority"]["source_identity"] == head.source_identity
    assert continuation["current_authority"]["generation"] == head.generation
    assert continuation["next_executable_phase"] == "phase-2"


def test_tampered_continuation_state_fails_verification(tmp_path: Path, monkeypatch) -> None:
    _root, publisher, head = _checkpoint(tmp_path, monkeypatch)
    receipt = subject._verify_receipt(publisher, head.checkpoint_receipt_sha256)
    path = publisher._path(receipt.continuation_object_key)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(RuntimeError, match="continuation state digest mismatch"):
        subject.verify(publisher=publisher)


def test_retry_with_changed_continuation_ledger_does_not_resume_stale_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    root = _source(tmp_path)
    identity = "sha256:" + "9" * 64
    calls = {"package": 0}
    monkeypatch.setattr(subject, "canonical_source_identity", lambda _root: identity)

    def package(output: Path, *, root: Path) -> str:
        del root
        calls["package"] += 1
        output.write_bytes(b"archive")
        return hashlib.sha256(b"archive").hexdigest()

    monkeypatch.setattr(subject, "package_slim", package)
    monkeypatch.setattr(subject, "archive_source_identity", lambda _archive: identity)

    class CrashBeforeCas(subject.FilesystemRecoveryPublisher):
        def compare_and_swap_head(self, **kwargs) -> bool:
            raise RuntimeError("crash before cas")

    store = tmp_path / "durable"
    with pytest.raises(RuntimeError, match="before cas"):
        subject.checkpoint(
            root=root,
            publisher=CrashBeforeCas(store),
            campaign_id="campaign",
            expected_generation=0,
            continuation_input=_continuation_input(
                qualification_state="INCOMPLETE", next_phase="phase-2"
            ),
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )
    assert calls["package"] == 1
    with pytest.raises(RuntimeError, match="before cas"):
        subject.checkpoint(
            root=root,
            publisher=CrashBeforeCas(store),
            campaign_id="campaign",
            expected_generation=0,
            continuation_input=_continuation_input(
                qualification_state="INCOMPLETE", next_phase="phase-3"
            ),
            proof_level="NONE",
            qualification_state="INCOMPLETE",
        )
    assert calls["package"] == 2


def test_markdown_handoff_is_generated_from_verified_committed_state(
    tmp_path: Path, monkeypatch
) -> None:
    _root, publisher, head = _checkpoint(tmp_path, monkeypatch)
    projection = subject.render_handoff(subject.status(publisher=publisher))
    assert "This Markdown is not authority" in projection
    assert head.source_identity in projection
    assert head.checkpoint_receipt_sha256 in projection
    assert head.continuation_state_sha256 in projection
    assert "phase-2" in projection
