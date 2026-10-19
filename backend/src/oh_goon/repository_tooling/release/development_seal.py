"""Fail-closed DEVELOPMENT lineage seal publication.

This owner deliberately knows nothing about ChatGPT Library or any remote artifact store.
The persistence adapter must first materialize the existing seal namespace into one local
seal directory. This module then provides the race-safe version/source collision cut:
one version may map to one exact lineage manifest, and historical seal bytes are never
overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Mapping

SEAL_SCHEMA = "oh-goon.development-seal/v1"
_VERSION = re.compile(r"\d+\.\d+\.\d+")
_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}")


class DevelopmentSealCollision(RuntimeError):
    """The requested DEVELOPMENT version already names different sealed bytes."""


def _required_sha(value: object, *, field: str) -> str:
    rendered = str(value or "")
    if _SHA256.fullmatch(rendered) is None:
        raise ValueError(f"{field} must be a SHA-256 identity")
    return rendered


def _required_version(value: object, *, field: str) -> str:
    rendered = str(value or "")
    if _VERSION.fullmatch(rendered) is None:
        raise ValueError(f"{field} must be canonical x.y.z")
    return rendered


def validate_development_seal(payload: Mapping[str, object]) -> str:
    """Validate the lineage facts required before a DEVELOPMENT seal can persist."""

    if payload.get("schema") != SEAL_SCHEMA:
        raise ValueError(f"development seal schema must be {SEAL_SCHEMA}")
    if payload.get("status") != "DEVELOPMENT":
        raise ValueError("development seal status must be DEVELOPMENT")
    version = _required_version(payload.get("version"), field="version")
    parent = payload.get("parent")
    candidate = payload.get("candidate")
    patch = payload.get("patch")
    if not all(isinstance(value, Mapping) for value in (parent, candidate, patch)):
        raise ValueError("development seal requires parent, candidate, and patch mappings")
    parent_version = _required_version(parent.get("version"), field="parent.version")
    _required_sha(parent.get("source_identity"), field="parent.source_identity")
    _required_sha(parent.get("zip_sha256"), field="parent.zip_sha256")
    _required_sha(candidate.get("source_identity"), field="candidate.source_identity")
    _required_sha(candidate.get("zip_sha256"), field="candidate.zip_sha256")
    if patch.get("from_version") != parent_version or patch.get("to_version") != version:
        raise ValueError("development seal patch lineage must match parent and candidate versions")
    _required_sha(patch.get("sha256"), field="patch.sha256")
    return version


def canonical_seal_bytes(payload: Mapping[str, object]) -> bytes:
    validate_development_seal(payload)
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def seal_path(seal_directory: Path, *, version: str) -> Path:
    _required_version(version, field="version")
    return seal_directory / f"OH-GOON-{version}-DEVELOPMENT-SEAL.json"


def publish_development_seal(seal_directory: Path, payload: Mapping[str, object]) -> Path:
    """Create one immutable version seal or prove an existing seal is byte-identical.

    O_EXCL is the concurrency authority. Two controllers racing the same version cannot
    both publish different identities. An already-existing identical seal is idempotent;
    any different bytes fail closed and require the caller to allocate another version.
    """

    version = validate_development_seal(payload)
    encoded = canonical_seal_bytes(payload)
    directory = seal_directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = seal_path(directory, version=version)
    if path.is_symlink():
        raise DevelopmentSealCollision(f"development seal path is a symlink: {path}")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags, 0o644)
    except FileExistsError as collision:
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise DevelopmentSealCollision(
                f"existing development seal cannot be read: {path}"
            ) from exc
        if existing != encoded:
            raise DevelopmentSealCollision(
                f"development version {version} already has a different sealed identity; "
                "allocate the next version"
            ) from collision
        return path
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish one immutable Oh-Goon DEVELOPMENT lineage seal"
    )
    parser.add_argument("--seal-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"manifest is unreadable: {exc}")
    if not isinstance(payload, dict):
        parser.error("manifest must contain a JSON object")
    path = publish_development_seal(args.seal_dir, payload)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
