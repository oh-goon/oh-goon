from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.core.models.game_tape import GameTapeProducerImplementationProvenance


class HashmarksRuntimeContractError(ValueError):
    """The loaded Hashmarks runtime does not match admitted producer evidence."""


def _hashmarks_version() -> str:
    from hashmarks import __version__

    return str(__version__)


def native_producer_implementation_identity() -> str:
    from hashmarks import native_producer_implementation_identity as native_owner

    return native_owner()


def loaded_hashmarks_version() -> str:
    return _hashmarks_version()


def loaded_hashmarks_producer_identity() -> str:
    try:
        return native_producer_implementation_identity()
    except (ImportError, OSError, ValueError) as exc:
        raise HashmarksRuntimeContractError(
            "Hashmarks is required for Oh-Goon repository evidence; the loaded runtime "
            "cannot establish its producer identity. Run make init with the committed dependency lock."
        ) from exc


def loaded_hashmarks_producer_provenance() -> GameTapeProducerImplementationProvenance | None:
    """Detailed diagnostics await a supported producer API; identity remains native."""
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
