from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel


class RuntimeAuthorityEvidenceKind(StrEnum):
    WATCHDOG = "WATCHDOG"
    TERMINATION = "TERMINATION"
    RACE = "RACE"
    RECOVERY = "RECOVERY"
    OUTPUT_ACCEPTANCE = "OUTPUT_ACCEPTANCE"
    SIDE_EFFECT = "SIDE_EFFECT"


class RuntimeAuthorityEvidence(ImmutableModel):
    """One bounded Game Tape projection of an already-authoritative runtime fact."""

    kind: RuntimeAuthorityEvidenceKind
    authority_identity: str = Field(min_length=1, max_length=160)
    execution_id: str = Field(min_length=1, max_length=160)
    generation: int = Field(ge=1)
    disposition: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=240)


class GameTapeRuntimeAuthorityReceipt(ImmutableModel):
    """Bounded diagnostic projection. This receipt never grants execution authority."""

    evidence: tuple[RuntimeAuthorityEvidence, ...] = Field(default_factory=tuple, max_length=256)
    observed_count: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    truncated: bool
    max_evidence: int = Field(default=256, ge=1, le=256)
    ui_execution_authority: Literal[False] = False

