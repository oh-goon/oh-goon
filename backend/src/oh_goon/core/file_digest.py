from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

_STREAM_CHUNK_BYTES = 1024 * 1024


class DigestSink(Protocol):
    def update(self, data: bytes, /) -> None: ...


def update_digest_from_file(digest: DigestSink, path: Path) -> int:
    """Stream one file into an existing digest and return its exact byte count."""

    byte_count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_STREAM_CHUNK_BYTES):
            digest.update(chunk)
            byte_count += len(chunk)
    return byte_count


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one file without materializing the file in memory."""

    digest = hashlib.sha256()
    update_digest_from_file(digest, path)
    return digest.hexdigest()


def sha256_file_with_size(path: Path) -> tuple[str, int]:
    """Return SHA-256 and exact byte count from one streaming pass."""

    digest = hashlib.sha256()
    byte_count = update_digest_from_file(digest, path)
    return digest.hexdigest(), byte_count


def read_prefix(path: Path, max_bytes: int) -> bytes:
    """Read only the bounded prefix required by a format/shebang probe."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    with path.open("rb") as stream:
        return stream.read(max_bytes)
