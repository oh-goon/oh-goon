from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

RecoveryKind = Literal[
    "process_group_cleanup",
    "timeout_refinement",
    "environment_retry",
    "sqlite_reproof",
]
RecoveryOutcome = Literal["requested", "succeeded", "failed", "blocked"]


@dataclass(frozen=True, slots=True)
class ExecutionRecoveryEvent:
    """Bounded non-authoritative evidence for one execution recovery decision."""

    kind: RecoveryKind
    action: str
    reason_code: str
    outcome: RecoveryOutcome
    work_identity: str | None = None
    parent_identity: str | None = None
    child_identities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def recovery_work_identity(parts: tuple[str, ...]) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
