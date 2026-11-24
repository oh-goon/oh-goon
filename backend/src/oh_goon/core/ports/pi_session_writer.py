"""Process-local port to the original claimed worker's Pi writer custody."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from oh_goon.core.models.agent_providers import ResolvedAgentModel
from oh_goon.core.models.pi_session_storage import (
    PiSessionDirectoryPin,
    PiSessionFileObservation,
    PiSessionNativeObservation,
    PiSessionSaveObservation,
    PiSessionWriterObservation,
)


@dataclass(frozen=True, slots=True)
class ReservedPiSessionWriter:
    directory: PiSessionDirectoryPin
    observe_file: Callable[[PiSessionSaveObservation], PiSessionFileObservation] = field(repr=False)
    settle: Callable[[PiSessionWriterObservation], None] = field(repr=False)
    prior_native: PiSessionNativeObservation | None = field(default=None, repr=False)


type PiSessionWriter = Callable[
    [str, int, ResolvedAgentModel], AbstractContextManager[ReservedPiSessionWriter]
]
