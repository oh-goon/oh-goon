from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
from enum import StrEnum

from oh_goon.repository_tooling.development_continuation import (
    CONTINUATION_INPUT_SCHEMA,
    CONTINUATION_SCHEMA,
    continuation_input_from_bytes as _continuation_input_from_bytes,
    continuation_input_sha256 as _continuation_input_sha256,
    continuation_state_bytes as _continuation_state_bytes,
    continuation_state_from_bytes as _continuation_state_from_bytes,
    render_handoff,
)
from oh_goon.repository_tooling.recovery_store import (
    HEAD_SCHEMA,
    PROOF_SCHEMA,
    RECEIPT_SCHEMA,
    FilesystemRecoveryPublisher,
    ProofReceipt,
    PublicationReceipt,
    RecoveryHead,
    RecoveryPublisher,
    head_from_bytes as _head_from_bytes,
    sha256_path as _sha256,
)
from oh_goon.repository_tooling.release.package_slim import (
    archive_source_identity,
    canonical_source_identity,
    package_slim,
)

PROOF_LEVELS = {"NONE", "FOCUSED_PROVEN", "BROAD_HOSTED_PROVEN", "NATIVE_VERIFIED", "CERTIFIED"}


class PublicationState(StrEnum):
    PLANNED = "PLANNED"
    BUILT = "BUILT"
    LOCALLY_VERIFIED = "LOCALLY_VERIFIED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    DURABLY_VERIFIED = "DURABLY_VERIFIED"
    RECEIPTED = "RECEIPTED"
    COMMITTED = "COMMITTED"


def _transition(hook: Callable[[PublicationState], None] | None, state: PublicationState) -> None:
    if hook is not None:
        hook(state)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def _require_external_recovery_path(*, source_root: Path, candidate: Path, label: str) -> Path:
    source_root = source_root.resolve()
    candidate = candidate.resolve()
    if candidate == source_root or source_root in candidate.parents:
        raise RuntimeError(f"{label} must be outside canonical source root: {candidate}")
    return candidate


def _read_head(publisher: RecoveryPublisher) -> tuple[RecoveryHead | None, str | None]:
    try:
        content = publisher.read("RECOVERY_HEAD.json")
    except FileNotFoundError:
        return None, None
    return _head_from_bytes(content), _digest(content)


def _transaction_id(
    *, campaign_id: str, parent_source_identity: str | None, result_source_identity: str,
    archive_sha256: str, continuation_state_sha256: str, generation: int,
) -> str:
    payload = _json_bytes({
        "archive_sha256": archive_sha256,
        "campaign_id": campaign_id,
        "continuation_state_sha256": continuation_state_sha256,
        "generation": generation,
        "parent_source_identity": parent_source_identity,
        "result_source_identity": result_source_identity,
    })
    return "sha256:" + _digest(payload)


def create_proof_receipt(
    *, source_identity: str, test_command_identity: str, test_selection: str,
    environment_identity: str, evidence_identity: str, timestamp: str, proof_level: str,
) -> bytes:
    if proof_level not in PROOF_LEVELS:
        raise RuntimeError(f"unsupported recovery proof level: {proof_level}")
    return _json_bytes(asdict(ProofReceipt(
        schema=PROOF_SCHEMA,
        source_identity=source_identity,
        test_command_identity=test_command_identity,
        test_selection=test_selection,
        environment_identity=environment_identity,
        evidence_identity=evidence_identity,
        timestamp=timestamp,
        proof_level=proof_level,
    )))


def _verify_archive_bytes(content: bytes, *, expected_sha: str, expected_size: int,
                          expected_source_identity: str) -> None:
    if len(content) != expected_size or _digest(content) != expected_sha:
        raise RuntimeError("durable recovery archive size/digest mismatch")
    with tempfile.TemporaryDirectory(prefix="oh-goon-recovery-readback-") as temp_dir:
        archive = Path(temp_dir) / "checkpoint.zip"
        archive.write_bytes(content)
        if archive_source_identity(archive) != expected_source_identity:
            raise RuntimeError("durable recovery archive source identity mismatch")


def _verify_receipt(publisher: RecoveryPublisher, receipt_sha: str) -> PublicationReceipt:
    key = f"receipts/sha256/{receipt_sha}"
    content = publisher.read(key)
    if _digest(content) != receipt_sha:
        raise RuntimeError("recovery publication receipt digest mismatch")
    payload = json.loads(content)
    if payload.get("schema") != RECEIPT_SCHEMA:
        raise RuntimeError("unsupported recovery publication receipt schema")
    return PublicationReceipt(**payload)


def _verify_continuation(
    publisher: RecoveryPublisher, *, receipt: PublicationReceipt, head: RecoveryHead | None = None,
) -> dict[str, Any]:
    expected_key = f"continuations/sha256/{receipt.continuation_state_sha256}"
    if receipt.continuation_object_key != expected_key:
        raise RuntimeError("recovery continuation object key mismatch")
    content = publisher.read(receipt.continuation_object_key)
    if _digest(content) != receipt.continuation_state_sha256:
        raise RuntimeError("recovery continuation state digest mismatch")
    payload = _continuation_state_from_bytes(content)
    authority = payload["current_authority"]
    expected_authority = {
        "campaign_id": receipt.campaign_id,
        "generation": receipt.generation,
        "parent_source_identity": receipt.parent_source_identity,
        "source_identity": receipt.result_source_identity,
        "archive_sha256": receipt.archive_sha256,
        "archive_size": receipt.archive_size,
        "object_key": receipt.object_key,
        "recovery_proof_receipt_sha256": receipt.proof_receipt_sha256,
        "proof_level": receipt.proof_level,
    }
    for field, expected in expected_authority.items():
        if authority.get(field) != expected:
            raise RuntimeError(f"recovery continuation authority mismatch: {field}")
    if payload.get("qualification_state") != receipt.qualification_state:
        raise RuntimeError("recovery continuation qualification state mismatch")
    if head is not None:
        if head.continuation_state_sha256 != receipt.continuation_state_sha256:
            raise RuntimeError("recovery HEAD/continuation identity mismatch")
        if head.source_identity != receipt.result_source_identity:
            raise RuntimeError("recovery HEAD/continuation source mismatch")
    return payload


def _checkpoint_lineage(
    publisher: RecoveryPublisher, *, root: Path, campaign_id: str, expected_generation: int,
    expected_head_sha256: str | None,
) -> tuple[str | None, str | None, int, str]:
    previous, observed_head_sha = _read_head(publisher)
    actual_generation = previous.generation if previous else 0
    if actual_generation != expected_generation:
        raise RuntimeError(
            f"recovery HEAD generation changed: expected {expected_generation}, got {actual_generation}"
        )
    if observed_head_sha != expected_head_sha256:
        raise RuntimeError("recovery HEAD identity changed")
    if previous and previous.campaign_id != campaign_id:
        raise RuntimeError("recovery HEAD belongs to a different campaign")
    return previous.source_identity if previous else None, observed_head_sha, actual_generation + 1, canonical_source_identity(root)


def _transaction_index_key(
    *, campaign_id: str, generation: int, parent_source_identity: str | None,
    result_source_identity: str, continuation_input_sha256: str,
) -> str:
    identity = _digest(_json_bytes({
        "campaign_id": campaign_id,
        "continuation_input_sha256": continuation_input_sha256,
        "generation": generation,
        "parent_source_identity": parent_source_identity,
        "result_source_identity": result_source_identity,
    }))
    return f"transactions/sha256/{identity}"


def _commit_recovery_head(
    publisher: RecoveryPublisher, *, campaign_id: str, generation: int,
    expected_generation: int, expected_head_sha256: str | None, receipt_sha: str,
    continuation_state_sha256: str, source_identity: str, qualification_state: str,
    proof_level: str, state_hook: Callable[[PublicationState], None] | None,
) -> RecoveryHead:
    head = RecoveryHead(
        schema=HEAD_SCHEMA, campaign_id=campaign_id, generation=generation,
        previous_head_sha256=expected_head_sha256, checkpoint_receipt_sha256=receipt_sha,
        continuation_state_sha256=continuation_state_sha256, source_identity=source_identity,
        qualification_state=qualification_state, proof_level=proof_level,
    )
    if not publisher.compare_and_swap_head(
        expected_generation=expected_generation, expected_head_sha256=expected_head_sha256,
        content=_json_bytes(asdict(head)),
    ):
        raise RuntimeError("recovery HEAD CAS rejected stale writer")
    _transition(state_hook, PublicationState.COMMITTED)
    return head


def _resume_published_transaction(
    publisher: RecoveryPublisher, *, index_key: str, campaign_id: str, generation: int,
    parent_source_identity: str | None, result_source_identity: str,
    continuation_input_sha256: str, expected_generation: int,
    expected_head_sha256: str | None,
    state_hook: Callable[[PublicationState], None] | None,
) -> RecoveryHead | None:
    try:
        receipt_sha = publisher.read(index_key).decode().strip()
    except FileNotFoundError:
        return None
    if not receipt_sha:
        return None
    receipt = _verify_receipt(publisher, receipt_sha)
    lineage = (
        receipt.campaign_id,
        receipt.generation,
        receipt.parent_source_identity,
        receipt.result_source_identity,
        receipt.continuation_input_sha256,
    )
    expected = (
        campaign_id, generation, parent_source_identity, result_source_identity,
        continuation_input_sha256,
    )
    if lineage != expected:
        raise RuntimeError("recovery transaction index lineage mismatch")
    stat = publisher.stat(receipt.object_key)
    if stat.size != receipt.archive_size or stat.sha256 != receipt.archive_sha256:
        raise RuntimeError("resumed recovery archive stat mismatch")
    _verify_archive_bytes(
        publisher.read(receipt.object_key), expected_sha=receipt.archive_sha256,
        expected_size=receipt.archive_size, expected_source_identity=result_source_identity,
    )
    _verify_continuation(publisher, receipt=receipt)
    return _commit_recovery_head(
        publisher, campaign_id=campaign_id, generation=generation,
        expected_generation=expected_generation, expected_head_sha256=expected_head_sha256,
        receipt_sha=receipt_sha, continuation_state_sha256=receipt.continuation_state_sha256,
        source_identity=result_source_identity, qualification_state=receipt.qualification_state,
        proof_level=receipt.proof_level,
        state_hook=state_hook,
    )


def _build_checkpoint_archive(
    root: Path, *, result_source_identity: str,
    state_hook: Callable[[PublicationState], None] | None,
) -> tuple[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="oh-goon-recovery-build-") as temp_dir:
        archive = Path(temp_dir) / "checkpoint.zip"
        archive_sha = package_slim(archive, root=root)
        _transition(state_hook, PublicationState.BUILT)
        if _sha256(archive) != archive_sha or archive_source_identity(archive) != result_source_identity:
            raise RuntimeError("locally verified recovery archive identity mismatch")
        archive_bytes = archive.read_bytes()
    _transition(state_hook, PublicationState.LOCALLY_VERIFIED)
    return archive_sha, archive_bytes


def _publish_checkpoint_archive(
    publisher: RecoveryPublisher, *, archive_sha: str, archive_bytes: bytes,
    result_source_identity: str, state_hook: Callable[[PublicationState], None] | None,
) -> str:
    object_key = f"objects/sha256/{archive_sha}"
    _transition(state_hook, PublicationState.PUBLISHING)
    publisher.publish_immutable(object_key, archive_bytes)
    _transition(state_hook, PublicationState.PUBLISHED)
    stat = publisher.stat(object_key)
    if stat.size != len(archive_bytes) or stat.sha256 != archive_sha:
        raise RuntimeError("durable recovery archive stat mismatch")
    _verify_archive_bytes(
        publisher.read(object_key), expected_sha=archive_sha, expected_size=len(archive_bytes),
        expected_source_identity=result_source_identity,
    )
    _transition(state_hook, PublicationState.DURABLY_VERIFIED)
    return object_key


def _publish_checkpoint_proof(
    publisher: RecoveryPublisher, *, proof_receipt: bytes | None, proof_level: str,
    result_source_identity: str,
) -> tuple[str | None, str]:
    if proof_receipt is None:
        return None, "NONE" if proof_level != "NONE" else proof_level
    proof = ProofReceipt(**json.loads(proof_receipt))
    if proof.schema != PROOF_SCHEMA or proof.source_identity != result_source_identity:
        raise RuntimeError("recovery proof receipt does not bind result source identity")
    if proof.proof_level != proof_level:
        raise RuntimeError("recovery proof receipt level mismatch")
    proof_sha = _digest(proof_receipt)
    key = f"proofs/sha256/{proof_sha}"
    publisher.publish_receipt(key, proof_receipt)
    if publisher.read(key) != proof_receipt:
        raise RuntimeError("durable recovery proof receipt mismatch")
    return proof_sha, proof_level


def _publish_checkpoint_continuation(
    publisher: RecoveryPublisher, *, root: Path, input_payload: dict[str, Any],
    campaign_id: str, generation: int, parent_source_identity: str | None,
    result_source_identity: str, archive_sha: str, archive_size: int, object_key: str,
    proof_sha: str | None, proof_level: str,
) -> tuple[str, str]:
    content = _continuation_state_bytes(
        root=root, input_payload=input_payload, campaign_id=campaign_id, generation=generation,
        parent_source_identity=parent_source_identity, result_source_identity=result_source_identity,
        archive_sha256=archive_sha, archive_size=archive_size, object_key=object_key,
        proof_receipt_sha256=proof_sha, proof_level=proof_level,
    )
    continuation_sha = _digest(content)
    key = f"continuations/sha256/{continuation_sha}"
    publisher.publish_immutable(key, content)
    stat = publisher.stat(key)
    if stat.size != len(content) or stat.sha256 != continuation_sha:
        raise RuntimeError("durable recovery continuation stat mismatch")
    if publisher.read(key) != content:
        raise RuntimeError("durable recovery continuation state mismatch")
    return continuation_sha, key


def _publish_checkpoint_receipt(
    publisher: RecoveryPublisher, *, index_key: str, transaction_id: str, campaign_id: str,
    generation: int, parent_source_identity: str | None, result_source_identity: str,
    archive_sha: str, archive_size: int, object_key: str, continuation_input_sha256: str,
    continuation_state_sha256: str, continuation_object_key: str, proof_sha: str | None,
    proof_level: str, qualification_state: str,
    state_hook: Callable[[PublicationState], None] | None,
) -> str:
    receipt = PublicationReceipt(
        schema=RECEIPT_SCHEMA, transaction_id=transaction_id, campaign_id=campaign_id,
        generation=generation, parent_source_identity=parent_source_identity,
        result_source_identity=result_source_identity, archive_sha256=archive_sha,
        archive_size=archive_size, object_key=object_key,
        continuation_input_sha256=continuation_input_sha256,
        continuation_state_sha256=continuation_state_sha256,
        continuation_object_key=continuation_object_key, proof_receipt_sha256=proof_sha,
        proof_level=proof_level, qualification_state=qualification_state,
    )
    receipt_bytes = _json_bytes(asdict(receipt))
    receipt_sha = _digest(receipt_bytes)
    receipt_key = f"receipts/sha256/{receipt_sha}"
    publisher.publish_receipt(receipt_key, receipt_bytes)
    if publisher.read(receipt_key) != receipt_bytes:
        raise RuntimeError("durable recovery publication receipt mismatch")
    publisher.publish_receipt(index_key, (receipt_sha + "\n").encode())
    if publisher.read(index_key).decode().strip() != receipt_sha:
        raise RuntimeError("durable recovery transaction index mismatch")
    _transition(state_hook, PublicationState.RECEIPTED)
    return receipt_sha


def checkpoint(
    *, root: Path, publisher: RecoveryPublisher, campaign_id: str, expected_generation: int,
    continuation_input: bytes, expected_head_sha256: str | None = None,
    proof_level: str = "FOCUSED_PROVEN",
    qualification_state: str = "NATIVE-QUALIFICATION-INCOMPLETE",
    proof_receipt: bytes | None = None,
    state_hook: Callable[[PublicationState], None] | None = None,
) -> RecoveryHead:
    if not publisher.supports_conditional_head:
        raise RuntimeError("recovery publisher lacks safe conditional HEAD mutation")
    if proof_level not in PROOF_LEVELS:
        raise RuntimeError(f"unsupported recovery proof level: {proof_level}")
    continuation_payload = _continuation_input_from_bytes(
        continuation_input, qualification_state=qualification_state,
    )
    continuation_input_sha = _continuation_input_sha256(continuation_payload)
    _transition(state_hook, PublicationState.PLANNED)
    parent_source_identity, observed_head_sha, generation, result_source_identity = _checkpoint_lineage(
        publisher, root=root, campaign_id=campaign_id, expected_generation=expected_generation,
        expected_head_sha256=expected_head_sha256,
    )
    index_key = _transaction_index_key(
        campaign_id=campaign_id, generation=generation,
        parent_source_identity=parent_source_identity, result_source_identity=result_source_identity,
        continuation_input_sha256=continuation_input_sha,
    )
    resumed = _resume_published_transaction(
        publisher, index_key=index_key, campaign_id=campaign_id, generation=generation,
        parent_source_identity=parent_source_identity, result_source_identity=result_source_identity,
        continuation_input_sha256=continuation_input_sha, expected_generation=expected_generation,
        expected_head_sha256=expected_head_sha256,
        state_hook=state_hook,
    )
    if resumed is not None:
        return resumed
    archive_sha, archive_bytes = _build_checkpoint_archive(
        root, result_source_identity=result_source_identity, state_hook=state_hook,
    )
    object_key = _publish_checkpoint_archive(
        publisher, archive_sha=archive_sha, archive_bytes=archive_bytes,
        result_source_identity=result_source_identity, state_hook=state_hook,
    )
    proof_sha, effective_proof_level = _publish_checkpoint_proof(
        publisher, proof_receipt=proof_receipt, proof_level=proof_level,
        result_source_identity=result_source_identity,
    )
    continuation_sha, continuation_key = _publish_checkpoint_continuation(
        publisher, root=root, input_payload=continuation_payload, campaign_id=campaign_id,
        generation=generation, parent_source_identity=parent_source_identity,
        result_source_identity=result_source_identity, archive_sha=archive_sha,
        archive_size=len(archive_bytes), object_key=object_key, proof_sha=proof_sha,
        proof_level=effective_proof_level,
    )
    transaction_id = _transaction_id(
        campaign_id=campaign_id, parent_source_identity=parent_source_identity,
        result_source_identity=result_source_identity, archive_sha256=archive_sha,
        continuation_state_sha256=continuation_sha, generation=generation,
    )
    receipt_sha = _publish_checkpoint_receipt(
        publisher, index_key=index_key, transaction_id=transaction_id, campaign_id=campaign_id,
        generation=generation, parent_source_identity=parent_source_identity,
        result_source_identity=result_source_identity, archive_sha=archive_sha,
        archive_size=len(archive_bytes), object_key=object_key,
        continuation_input_sha256=continuation_input_sha,
        continuation_state_sha256=continuation_sha, continuation_object_key=continuation_key,
        proof_sha=proof_sha, proof_level=effective_proof_level,
        qualification_state=qualification_state, state_hook=state_hook,
    )
    return _commit_recovery_head(
        publisher, campaign_id=campaign_id, generation=generation,
        expected_generation=expected_generation, expected_head_sha256=expected_head_sha256,
        receipt_sha=receipt_sha, continuation_state_sha256=continuation_sha,
        source_identity=result_source_identity, qualification_state=qualification_state,
        proof_level=effective_proof_level, state_hook=state_hook,
    )


def verify(*, publisher: RecoveryPublisher) -> RecoveryHead:
    head, _head_sha = _read_head(publisher)
    if head is None:
        raise RuntimeError("recovery HEAD is missing")
    receipt = _verify_receipt(publisher, head.checkpoint_receipt_sha256)
    if (
        receipt.campaign_id != head.campaign_id
        or receipt.generation != head.generation
        or receipt.result_source_identity != head.source_identity
        or receipt.proof_level != head.proof_level
        or receipt.qualification_state != head.qualification_state
    ):
        raise RuntimeError("recovery HEAD/receipt lineage mismatch")
    stat = publisher.stat(receipt.object_key)
    if stat.size != receipt.archive_size or stat.sha256 != receipt.archive_sha256:
        raise RuntimeError("recovery HEAD archive stat mismatch")
    _verify_archive_bytes(
        publisher.read(receipt.object_key), expected_sha=receipt.archive_sha256,
        expected_size=receipt.archive_size, expected_source_identity=head.source_identity,
    )
    _verify_continuation(publisher, receipt=receipt, head=head)
    if receipt.proof_receipt_sha256:
        proof_bytes = publisher.read(f"proofs/sha256/{receipt.proof_receipt_sha256}")
        if _digest(proof_bytes) != receipt.proof_receipt_sha256:
            raise RuntimeError("recovery proof receipt digest mismatch")
        proof = ProofReceipt(**json.loads(proof_bytes))
        if proof.source_identity != head.source_identity or proof.proof_level != head.proof_level:
            raise RuntimeError("recovery proof receipt source/level mismatch")
    return head


def status(*, publisher: RecoveryPublisher) -> dict[str, Any]:
    head = verify(publisher=publisher)
    receipt = _verify_receipt(publisher, head.checkpoint_receipt_sha256)
    continuation = _verify_continuation(publisher, receipt=receipt, head=head)
    return {"head": asdict(head), "continuation": continuation}


def resume(*, publisher: RecoveryPublisher, destination: Path) -> RecoveryHead:
    head = verify(publisher=publisher)
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError("recovery destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    receipt = _verify_receipt(publisher, head.checkpoint_receipt_sha256)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as stream:
        stream.write(publisher.read(receipt.object_key))
        archive = Path(stream.name)
    try:
        shutil.unpack_archive(str(archive), destination)
    finally:
        archive.unlink(missing_ok=True)
    roots = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(roots) != 1 or canonical_source_identity(roots[0]) != head.source_identity:
        raise RuntimeError("resumed source identity mismatch")
    return head


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Own durable recovery publication identity and CAS HEAD"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("checkpoint")
    create.add_argument("--root", type=Path, default=Path.cwd())
    create.add_argument("--store", type=Path, required=True)
    create.add_argument("--campaign-id", required=True)
    create.add_argument("--expected-generation", type=int, required=True)
    create.add_argument("--expected-head-sha256")
    create.add_argument("--continuation-input", type=Path, required=True)
    create.add_argument("--proof-level", default="NONE")
    create.add_argument("--qualification-state", default="NATIVE-QUALIFICATION-INCOMPLETE")
    create.add_argument("--format", choices=("json", "markdown"), default="json")
    check = sub.add_parser("verify")
    check.add_argument("--store", type=Path, required=True)
    check.add_argument("--subject-root", type=Path)
    check.add_argument("--format", choices=("json", "markdown"), default="json")
    restore = sub.add_parser("resume")
    restore.add_argument("--store", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--subject-root", type=Path)
    restore.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "checkpoint":
        root = args.root.resolve()
        store = _require_external_recovery_path(
            source_root=root, candidate=args.store, label="recovery store"
        )
        continuation_input = _require_external_recovery_path(
            source_root=root, candidate=args.continuation_input, label="recovery continuation input"
        )
        publisher = FilesystemRecoveryPublisher(store)
        checkpoint(
            root=root, publisher=publisher, campaign_id=args.campaign_id,
            expected_generation=args.expected_generation,
            continuation_input=continuation_input.read_bytes(),
            expected_head_sha256=args.expected_head_sha256, proof_level=args.proof_level,
            qualification_state=args.qualification_state,
        )
        observed = status(publisher=publisher)
    elif args.command == "verify":
        store = args.store.resolve()
        if args.subject_root is not None:
            store = _require_external_recovery_path(
                source_root=args.subject_root, candidate=store, label="recovery store"
            )
        publisher = FilesystemRecoveryPublisher(store)
        observed = status(publisher=publisher)
    else:
        store = args.store.resolve()
        destination = args.destination.resolve()
        if args.subject_root is not None:
            store = _require_external_recovery_path(
                source_root=args.subject_root, candidate=store, label="recovery store"
            )
            destination = _require_external_recovery_path(
                source_root=args.subject_root, candidate=destination, label="recovery destination"
            )
        publisher = FilesystemRecoveryPublisher(store)
        resume(publisher=publisher, destination=destination)
        observed = status(publisher=publisher)
    if args.format == "markdown":
        print(render_handoff(observed), end="")
    else:
        print(json.dumps(observed, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
