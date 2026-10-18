from __future__ import annotations

from typing import Protocol

from oh_goon.core.models.diagnostic import DiagnosticEvent


class DiagnosticEmitter(Protocol):
    """Passive diagnostic boundary; emitters cannot control domain lifecycle."""

    def emit(self, event: DiagnosticEvent) -> None: ...
