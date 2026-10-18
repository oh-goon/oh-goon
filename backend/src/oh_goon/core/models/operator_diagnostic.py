from __future__ import annotations

from typing import Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel


class OperatorWhyNotDiagnostic(ImmutableModel):
    """One operator-facing projection over an existing fail-closed authority fact."""

    authority_owner: str = Field(min_length=1, max_length=160)
    state: Literal["READY", "BLOCKED", "NOT_PROBED", "NOT_APPLICABLE", "RUNTIME_DEPENDENT"]
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,119}$")
    current_state: str = Field(min_length=1, max_length=1000)
    required_state: str = Field(min_length=1, max_length=1000)
    safe_next_action: str | None = Field(default=None, min_length=1, max_length=500)
    evidence_hint: str | None = Field(default=None, min_length=1, max_length=500)
    retryable: bool = False
