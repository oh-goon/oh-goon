"""Run-owned notes capability consumed only by the selected native adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from oh_goon.core.models.agent_notes import AgentNoteInput, AgentNotePage, AgentNoteReceipt
from oh_goon.core.models.agent_providers import ResolvedAgentModel


class AgentNotesTool(Protocol):
    def require_active(self) -> None: ...

    def publish(self, note: AgentNoteInput) -> AgentNoteReceipt: ...

    def read(self, after: int) -> AgentNotePage: ...

    def acknowledge(self, note_ids: tuple[int, ...]) -> None: ...


AgentNotesForStep = Callable[[str, int, ResolvedAgentModel], AgentNotesTool]
