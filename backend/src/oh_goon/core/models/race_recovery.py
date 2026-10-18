from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import stable_id


class TerminalAuthority(StrEnum):
    COMPLETION = "COMPLETION"
    TIMEOUT = "TIMEOUT"
    CANCEL = "CANCEL"
    TRUST_REVOKE = "TRUST_REVOKE"
    LEASE_EXPIRED = "LEASE_EXPIRED"


class TerminalClaim(ImmutableModel):
    execution_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    authority: TerminalAuthority
    sequence: int = Field(ge=1)
    evidence_id: str = Field(min_length=1)

    @property
    def claim_id(self) -> str:
        return stable_id("terminal-claim", self.model_dump(mode="json", exclude_computed_fields=True))


class RecoveryBudget(ImmutableModel):
    lineage_id: str = Field(min_length=1)
    max_attempts: int = Field(ge=1, le=32)
    attempts_consumed: int = Field(ge=0, le=32)
    max_runtime_seconds: int = Field(ge=1, le=604_800)
    runtime_consumed_seconds: int = Field(ge=0, le=604_800)

    @model_validator(mode="after")
    def _within_declared_budget(self) -> "RecoveryBudget":
        if self.attempts_consumed > self.max_attempts:
            raise ValueError("attempt consumption exceeds cumulative recovery budget")
        if self.runtime_consumed_seconds > self.max_runtime_seconds:
            raise ValueError("runtime consumption exceeds cumulative recovery budget")
        return self

    def consume(self, *, runtime_seconds: int) -> "RecoveryBudget":
        if runtime_seconds < 0:
            raise ValueError("runtime consumption cannot be negative")
        return RecoveryBudget(
            lineage_id=self.lineage_id,
            max_attempts=self.max_attempts,
            attempts_consumed=self.attempts_consumed + 1,
            max_runtime_seconds=self.max_runtime_seconds,
            runtime_consumed_seconds=self.runtime_consumed_seconds + runtime_seconds,
        )

    @property
    def exhausted(self) -> bool:
        return self.attempts_consumed >= self.max_attempts or self.runtime_consumed_seconds >= self.max_runtime_seconds


class WorkMembership(ImmutableModel):
    lineage_id: str = Field(min_length=1)
    expected_work_ids: frozenset[str] = Field(min_length=1)
    committed_work_ids: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _conserved(self) -> "WorkMembership":
        extras = self.committed_work_ids - self.expected_work_ids
        if extras:
            raise ValueError("committed work is outside the admitted membership")
        return self

    def commit(self, work_id: str) -> "WorkMembership":
        if work_id not in self.expected_work_ids:
            raise ValueError("work unit was not admitted into this lineage")
        return WorkMembership(
            lineage_id=self.lineage_id,
            expected_work_ids=self.expected_work_ids,
            committed_work_ids=self.committed_work_ids | {work_id},
        )

    @property
    def complete(self) -> bool:
        return self.committed_work_ids == self.expected_work_ids
