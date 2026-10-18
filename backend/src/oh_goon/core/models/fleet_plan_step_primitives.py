from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel

PLAN_TIMEOUT_RETURN_CODE = 124
PLAN_CONTRACT_RETURN_CODE = 125
PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE = 126
PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE = 127


def _validate_success_codes(value: tuple[int, ...]) -> tuple[int, ...]:
    if not value:
        raise ValueError("Fleet plan success_codes must not be empty")
    if len(value) != len(set(value)):
        raise ValueError("Fleet plan success_codes must be unique")
    if 0 not in value:
        raise ValueError("Fleet plan success_codes must include 0")
    reserved = sorted(
        set(value)
        & {
            PLAN_TIMEOUT_RETURN_CODE,
            PLAN_CONTRACT_RETURN_CODE,
            PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
            PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
        }
    )
    if reserved:
        raise ValueError(
            f"Fleet plan success_codes cannot include Fleet-reserved failure codes: {', '.join(map(str, reserved))}"
        )
    return value


class StepFailurePolicy(StrEnum):
    STOP = "stop"
    CONTINUE = "continue"


class StepLifecycle(StrEnum):
    NORMAL = "normal"
    FINALIZER = "finalizer"


class PlanPublishedOutput(ImmutableModel):
    """One non-secret scalar value published from a repository-relative text file."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    path: str = Field(min_length=1, max_length=240)
    max_bytes: int = Field(default=4096, ge=1, le=65536)

    @field_validator("path")
    @classmethod
    def validate_static_path(cls, value: str) -> str:
        if "${{ steps." in value:
            raise ValueError("Fleet published output paths cannot depend on runtime step outputs")
        return value


class PlanStreamOutput(ImmutableModel):
    """A bounded scalar from this exact native attempt's captured output."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    stream: Literal["stdout", "stderr"]
    max_bytes: int = Field(default=4096, ge=1, le=65536)


class PlanArtifactDeclaration(ImmutableModel):
    """One declared artifact path with explicit retention semantics."""

    path: str = Field(min_length=1, max_length=240)
    retention: Literal["evidence", "content_store"] = "evidence"

    @field_validator("path")
    @classmethod
    def validate_static_path(cls, value: str) -> str:
        if "${{ steps." in value:
            raise ValueError("Fleet artifact paths cannot depend on runtime step outputs")
        return value


class PathExistsCondition(ImmutableModel):
    kind: Literal["path_exists"] = "path_exists"
    path: str = Field(min_length=1, max_length=240)


class PathMissingCondition(ImmutableModel):
    kind: Literal["path_missing"] = "path_missing"
    path: str = Field(min_length=1, max_length=240)


class StepStatusCondition(ImmutableModel):
    kind: Literal["step_status"] = "step_status"
    step_id: str = Field(min_length=1, max_length=80)
    statuses: tuple[Literal["COMPLETED", "SKIPPED", "FAILED"], ...] = Field(
        min_length=1, max_length=3
    )

    @field_validator("statuses")
    @classmethod
    def validate_statuses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Fleet plan condition statuses must be unique")
        return value


StepCondition = Annotated[
    PathExistsCondition | PathMissingCondition | StepStatusCondition,
    Field(discriminator="kind"),
]


class StepRetryPolicy(ImmutableModel):
    """Explicit known-transient replay policy with a fully declared delay schedule.

    There is intentionally no inferred transient classification, exponential formula,
    or jitter. A plan author names the completed exit codes that are safe to replay,
    explains why, and declares every delay between attempts.
    """

    max_attempts: int = Field(default=1, ge=1, le=5)
    known_transient_exit_codes: tuple[int, ...] = Field(default_factory=tuple, max_length=16)
    reason: str | None = Field(default=None, min_length=1, max_length=240)
    delays_seconds: tuple[float, ...] = Field(default_factory=tuple, max_length=4)

    @field_validator("known_transient_exit_codes")
    @classmethod
    def validate_known_transient_exit_codes(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Fleet plan known_transient_exit_codes must be unique")
        if 0 in value:
            raise ValueError("Fleet plan known_transient_exit_codes cannot include 0")
        return value

    @field_validator("delays_seconds")
    @classmethod
    def validate_delays(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(delay < 0 or delay > 300 for delay in value):
            raise ValueError("Fleet plan retry delays must be between 0 and 300 seconds")
        return value

    @model_validator(mode="after")
    def validate_attempts(self) -> StepRetryPolicy:
        if self.max_attempts == 1:
            if self.known_transient_exit_codes or self.reason is not None or self.delays_seconds:
                raise ValueError(
                    "Fleet plan retry policy with max_attempts=1 cannot declare transient codes, reason, or delays"
                )
            return self
        if not self.known_transient_exit_codes:
            raise ValueError("Fleet plan retries require known_transient_exit_codes")
        if self.reason is None:
            raise ValueError("Fleet plan retries require an explicit reason")
        expected_delays = self.max_attempts - 1
        if len(self.delays_seconds) != expected_delays:
            raise ValueError(
                f"Fleet plan retries with max_attempts={self.max_attempts} require exactly {expected_delays} declared delays_seconds values"
            )
        return self

    def delay_after(self, attempt_number: int) -> float:
        """Return the already-admitted delay after one retryable failed attempt."""
        if attempt_number < 1 or attempt_number >= self.max_attempts:
            raise ValueError("Retry delay is defined only between admitted attempts")
        return self.delays_seconds[attempt_number - 1]


class MissingConfigPolicy(StrEnum):
    SKIP = "skip"
    FAIL = "fail"


def _validate_published_outputs(
    value: tuple[PlanPublishedOutput | PlanStreamOutput, ...],
) -> tuple[PlanPublishedOutput | PlanStreamOutput, ...]:
    names = [item.name for item in value]
    if len(names) != len(set(names)):
        raise ValueError("Fleet plan published output names must be unique per step")
    return value
