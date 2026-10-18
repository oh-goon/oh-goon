from __future__ import annotations

from pathlib import PurePosixPath

from oh_goon.core.models.game_tape import (
    GameTapeProducerImplementationInput,
    GameTapeProducerImplementationProvenance,
)


class HashmarksRuntimeContractError(ValueError):
    """The loaded Hashmarks runtime does not match admitted producer evidence."""


def _hashmarks_version() -> str:
    from hashmarks import __version__

    return str(__version__)


def native_producer_implementation_identity() -> str:
    from hashmarks.producer_identity import native_producer_implementation_identity as native_owner

    return native_owner()


def producer_implementation_provenance() -> object:
    from hashmarks.producer_identity import producer_implementation_provenance as provenance_owner

    return provenance_owner()


def loaded_hashmarks_version() -> str:
    return _hashmarks_version()


def loaded_hashmarks_producer_identity() -> str:
    try:
        return native_producer_implementation_identity()
    except ValueError as exc:
        raise HashmarksRuntimeContractError(
            "Hashmarks runtime is not materialized as an installed package"
        ) from exc


def loaded_hashmarks_producer_provenance() -> GameTapeProducerImplementationProvenance | None:
    """Translate producer-owned diagnostics without recreating producer identity semantics."""

    try:
        raw = producer_implementation_provenance()
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    producer_schema = raw.get("schema")
    identity_schema = raw.get("identity_schema")
    implementation_identity = raw.get("implementation_identity")
    if not isinstance(producer_schema, str) or not producer_schema:
        return None
    if not isinstance(identity_schema, str) or not identity_schema:
        return None
    if not isinstance(implementation_identity, str):
        return None
    if raw.get("authority") != "non-authoritative-explanation":
        return None
    if raw.get("storage") != "derived-not-persisted":
        return None
    inputs = raw.get("inputs")
    if not isinstance(inputs, list) or raw.get("input_count") != len(inputs):
        return None
    projected_inputs: list[GameTapeProducerImplementationInput] = []
    for row in inputs:
        if not isinstance(row, dict):
            return None
        path = row.get("path")
        byte_count = row.get("bytes")
        content_sha256 = row.get("content_sha256")
        if not isinstance(path, str) or not path or "\\" in path:
            return None
        relative = PurePosixPath(path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            return None
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            return None
        if not isinstance(content_sha256, str):
            return None
        try:
            projected_inputs.append(
                GameTapeProducerImplementationInput(
                    path=path,
                    byte_count=byte_count,
                    content_sha256=content_sha256,
                )
            )
        except ValueError:
            return None
    try:
        return GameTapeProducerImplementationProvenance(
            producer_schema=producer_schema,
            authority="non-authoritative-explanation",
            storage="derived-not-persisted",
            identity_schema=identity_schema,
            implementation_identity=implementation_identity,
            input_count=len(projected_inputs),
            inputs=tuple(projected_inputs),
        )
    except ValueError:
        return None


def require_loaded_hashmarks_runtime(*, expected_implementation_identity: str | None = None) -> str:
    """Return loaded producer identity and fail closed on evidence mismatch.

    Dependency selection/version/artifact authority belongs to pyproject.toml + uv.lock.
    Evidence compatibility is compared with the producer identity reported by the
    actually loaded Hashmarks package, not copied release constants.
    """

    implementation_identity = loaded_hashmarks_producer_identity()
    if (
        expected_implementation_identity is not None
        and implementation_identity != expected_implementation_identity
    ):
        raise HashmarksRuntimeContractError(
            "Hashmarks evidence producer implementation identity does not match the loaded Hashmarks runtime"
        )
    return implementation_identity
