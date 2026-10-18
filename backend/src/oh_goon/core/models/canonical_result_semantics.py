from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary.result_semantics import CanonicalResultStatus, FailureImpact


class CanonicalAcceptedResult(ImmutableModel):
    acceptance_id: str = Field(min_length=1)
    result_identity: str = Field(min_length=1)
    status: CanonicalResultStatus


class CanonicalChildResult(ImmutableModel):
    import_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    failure_impact: FailureImpact
    factual_status: CanonicalResultStatus
    parent_status: CanonicalResultStatus
    failed_step_ids: tuple[str, ...] = ()


class CanonicalPlanResult(ImmutableModel):
    plan_id: str = Field(min_length=1)
    status: CanonicalResultStatus
    failed_step_ids: tuple[str, ...] = ()
    children: tuple[CanonicalChildResult, ...] = ()
