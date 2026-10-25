"""The selected Skill view over an already prepared native Git source."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.models.execution_lock_models import LockedSkillSource


@dataclass(frozen=True, slots=True)
class PreparedSkill:
    root: Path
    path: str
    source: LockedSkillSource

    @property
    def reference(self) -> str:
        return f"git:{self.source.source}@{self.source.commit}:{self.path}"
