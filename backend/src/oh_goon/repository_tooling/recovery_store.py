from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

HEAD_SCHEMA = "oh-goon-recovery-head-v3"
RECEIPT_SCHEMA = "oh-goon-recovery-publication-v2"
PROOF_SCHEMA = "oh-goon-recovery-proof-v1"


@dataclass(frozen=True)
class RecoveryHead:
    schema: str
    campaign_id: str
    generation: int
    previous_head_sha256: str | None
    checkpoint_receipt_sha256: str
    continuation_state_sha256: str
    source_identity: str
    qualification_state: str
    proof_level: str


@dataclass(frozen=True)
class PublicationReceipt:
    schema: str
    transaction_id: str
    campaign_id: str
    generation: int
    parent_source_identity: str | None
    result_source_identity: str
    archive_sha256: str
    archive_size: int
    object_key: str
    continuation_input_sha256: str
    continuation_state_sha256: str
    continuation_object_key: str
    proof_receipt_sha256: str | None
    proof_level: str
    qualification_state: str


@dataclass(frozen=True)
class ProofReceipt:
    schema: str
    source_identity: str
    test_command_identity: str
    test_selection: str
    environment_identity: str
    evidence_identity: str
    timestamp: str
    proof_level: str


@dataclass(frozen=True)
class DurableObjectStat:
    size: int
    sha256: str


class RecoveryPublisher(Protocol):
    supports_conditional_head: bool

    def publish_immutable(self, key: str, content: bytes) -> None: ...
    def stat(self, key: str) -> DurableObjectStat: ...
    def read(self, key: str) -> bytes: ...
    def publish_receipt(self, key: str, content: bytes) -> None: ...
    def compare_and_swap_head(
        self,
        *,
        expected_generation: int,
        expected_head_sha256: str | None,
        content: bytes,
    ) -> bool: ...


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def head_from_bytes(content: bytes) -> RecoveryHead:
    payload = json.loads(content)
    if payload.get("schema") != HEAD_SCHEMA:
        raise RuntimeError(f"unsupported recovery HEAD schema: {payload.get('schema')!r}")
    return RecoveryHead(**payload)


class FilesystemRecoveryPublisher:
    """Durable filesystem adapter; recovery semantics remain backend-neutral."""

    supports_conditional_head = True

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise RuntimeError("recovery publisher key escapes store")
        return path

    def _sync_parent(self, path: Path) -> None:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write_immutable(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise RuntimeError(f"immutable recovery object conflicts: {key}")
            return
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise RuntimeError(f"immutable recovery object conflicts: {key}")
            self._sync_parent(path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def publish_immutable(self, key: str, content: bytes) -> None:
        self._write_immutable(key, content)

    def publish_receipt(self, key: str, content: bytes) -> None:
        self._write_immutable(key, content)

    def stat(self, key: str) -> DurableObjectStat:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return DurableObjectStat(size=path.stat().st_size, sha256=sha256_path(path))

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def compare_and_swap_head(
        self,
        *,
        expected_generation: int,
        expected_head_sha256: str | None,
        content: bytes,
    ) -> bool:
        lock = self._path(".RECOVERY_HEAD.lock")
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError("recovery HEAD CAS is already in progress") from exc
        os.close(lock_fd)
        try:
            head_path = self._path("RECOVERY_HEAD.json")
            current = head_path.read_bytes() if head_path.exists() else None
            current_sha = hashlib.sha256(current).hexdigest() if current is not None else None
            current_generation = head_from_bytes(current).generation if current is not None else 0
            if current_generation != expected_generation or current_sha != expected_head_sha256:
                return False
            fd, temporary = tempfile.mkstemp(prefix=".RECOVERY_HEAD.", dir=head_path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, head_path)
                self._sync_parent(head_path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return True
        finally:
            lock.unlink(missing_ok=True)
