"""Redacted historical evidence for an original persistent Pi Run."""

from typing import Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_evidence import PlanStepSettlementEvidence
from oh_goon.core.models.pi_session_storage import (
    PiSessionContinuationOrigin,
    PiSessionNativeObservation,
)
from oh_goon.core.models.source_authority import RepositorySourceAuthority


class GameTapePiFollowingStep(ImmutableModel):
    step_id: str
    settlement: PlanStepSettlementEvidence | None = None


class GameTapePiSessionEvidence(ImmutableModel):
    """Recorded custody and Step facts. Availability is not checked by playback."""

    mode: Literal["new", "continue"]
    prior_run_id: str | None = None
    prior_writer: PiSessionContinuationOrigin | None = None
    repository_id: str
    repository_source: RepositorySourceAuthority | None = None
    step_id: str
    implementation_identity: str = Field(pattern=r"^pi:sha256:[0-9a-f]{64}$")
    handoff_identity: str
    delivery_sha256: str
    skill_identity: str | None = None
    writer_reservation_sequence: int | None = Field(default=None, ge=1)
    writer_observation_sequence: int | None = Field(default=None, ge=1)
    quiescent_sequence: int | None = Field(default=None, ge=1)
    persistence: Literal["not_recorded", "unknown", "observed"]
    byte_identity: Literal["digest_recorded", "metadata_only"] | None = None
    availability: Literal["not_checked"] = "not_checked"
    native: PiSessionNativeObservation | None = None
    settlement: PlanStepSettlementEvidence | None = None
    following_steps: tuple[GameTapePiFollowingStep, ...] = Field(default=(), max_length=8)
    omitted_following_steps: int = Field(default=0, ge=0)
    refusal_code: str | None = None
    safe_next_action: str | None = None
