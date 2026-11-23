"""Process-local transport of one original Pi session directory handle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.errors import PiSessionRefusalCode
from oh_goon.core.models.base import ImmutableModel

PI_SESSION_SANDBOX_DIRECTORY = PurePosixPath("/run/oh-goon-pi-session")
PI_SESSION_NEW_FILE = PI_SESSION_SANDBOX_DIRECTORY / "session.jsonl"
PI_SESSION_OBSERVATION_RECORD = "oh_goon_pi_session_observation"
PI_SESSION_REFUSAL_RECORD = "oh_goon_pi_session_refusal"


@dataclass(frozen=True, slots=True)
class PiSessionFilePin:
    """The exact retained file borrowed for one continued launch."""

    fd: int = field(repr=False)
    require_before_launch: Callable[[], None] = field(repr=False)


@dataclass(frozen=True, slots=True)
class PiSessionDirectoryPin:
    """A borrowed directory handle retained by the original storage scope."""

    path: Path
    directory_fd: int = field(repr=False)
    workspace: Path
    require_current: Callable[[], None] = field(repr=False)
    existing_file: PiSessionFilePin | None = field(default=None, repr=False)


class PiSessionFileObservation(ImmutableModel):
    """Bounded native file metadata observed under the original directory pin."""

    device: int = Field(ge=0, strict=True)
    inode: int = Field(gt=0, strict=True)
    size: int = Field(ge=0, strict=True)
    sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )


class PiSessionRetirementDirectoryIdentity(ImmutableModel):
    """The retained directory whose exact file absence can be verified on retry."""

    device: int = Field(ge=0, strict=True)
    inode: int = Field(gt=0, strict=True)


@dataclass(frozen=True, slots=True)
class PiSessionRetirementPin:
    """Process-local custody for the Journal's explicitly authorized removal."""

    directory: PiSessionRetirementDirectoryIdentity
    remove: Callable[[], None] = field(repr=False)


class PiSessionNativeObservation(ImmutableModel):
    """Pi's correlated final identity and leaf, without conversation contents."""

    session_id: str = Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_-]+$")
    leaf_id: str = Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_-]+$")


class PiSessionInspection(ImmutableModel):
    """Bounded metadata and saved-file availability at one original Journal cut."""

    fleet_run_id: str = Field(min_length=1, max_length=160)
    native: PiSessionNativeObservation | None = None
    availability: Literal["available", "unavailable", "unverified", "busy"]
    retirement: Literal["none", "pending", "verified"] = "none"
    refusal_code: PiSessionRefusalCode | None = None


class PiSessionContinuationOrigin(ImmutableModel):
    """The exact prior Run and its observed writer cut, never a filesystem selector."""

    prior_run_id: str = Field(min_length=1, max_length=160)
    reservation_sequence: int = Field(ge=1)
    observation_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def require_observed_reservation(self) -> PiSessionContinuationOrigin:
        if self.observation_sequence <= self.reservation_sequence:
            raise ValueError("Pi continuation observation must follow its reservation")
        return self


class PiSessionWriterObservation(ImmutableModel):
    """Persistence observation after the exact native writer became quiescent."""

    physical_execution_id: str = Field(min_length=1, max_length=160)
    persistence: Literal["observed", "unknown"]
    file: PiSessionFileObservation | None = None
    session_id: str | None = None
    leaf_id: str | None = None
    file_name: str | None = None

    @model_validator(mode="after")
    def require_complete_observation(self) -> PiSessionWriterObservation:
        evidence = (self.file, self.session_id, self.leaf_id, self.file_name)
        if self.persistence == "unknown":
            if any(value is not None for value in evidence):
                raise ValueError("Unknown Pi persistence cannot claim native evidence")
        else:
            if self.session_id is None or self.leaf_id is None:
                raise ValueError("Observed Pi persistence requires its reported identity and leaf")
            PiSessionNativeObservation(session_id=self.session_id, leaf_id=self.leaf_id)
            if (
                self.file is None
                or self.file.size == 0
                or self.file_name != PI_SESSION_NEW_FILE.name
            ):
                raise ValueError(
                    "Observed Pi persistence requires the exact original nonempty file"
                )
        return self
