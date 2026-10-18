from __future__ import annotations

from pydantic import Field

from oh_goon.core.errors import ErrorKind, FleetDomainError
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import BackgroundFailureStage


class BackgroundFailure(ImmutableModel):
    """Typed immutable process-local failure record for managed background workers."""

    code: str = Field(min_length=1)
    kind: ErrorKind
    retryable: bool = False
    stage: BackgroundFailureStage
    message: str = Field(min_length=1)
    observed_at: str = Field(default_factory=lambda: utc_now().isoformat())

    @classmethod
    def from_exception(
        cls, exc: BaseException, *, stage: BackgroundFailureStage
    ) -> BackgroundFailure:
        if isinstance(exc, FleetDomainError):
            return cls(
                code=exc.code,
                kind=exc.kind,
                retryable=exc.retryable,
                stage=stage,
                message=str(exc) or exc.__class__.__name__,
            )
        return cls(
            code="UNEXPECTED_BACKGROUND_FAILURE",
            kind=ErrorKind.INTERNAL,
            retryable=False,
            stage=stage,
            message=str(exc) or exc.__class__.__name__,
        )
