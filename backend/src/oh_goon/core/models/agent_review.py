"""Source-bound Agent claims. These records confer no execution or certification authority."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import stable_id

if TYPE_CHECKING:
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

MAX_REVIEW_BYTES = 65536


def decode_agent_record(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_REVIEW_BYTES:
        raise FleetPlanInvalid("Agent record exceeds its byte budget")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise FleetPlanInvalid("Agent record has duplicate fields")
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=pairs)
    except (ValueError, TypeError) as exc:
        raise FleetPlanInvalid("Agent record must be one JSON object") from exc
    if not isinstance(data, dict):
        raise FleetPlanInvalid("Agent record must be one JSON object")
    return data


def review_scope(handoff: FrozenAgentStepHandoff) -> str:
    return stable_id(
        "agent-review-scope-v1",
        {
            "repository": handoff.source.repository_reference,
            "members": [item.path for item in handoff.source.repository.members],
        },
    )


class ReviewEvidence(ImmutableModel):
    path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    line: int = Field(strict=True, ge=1)


class ReviewFinding(ImmutableModel):
    finding_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    explanation: str = Field(min_length=1, max_length=4000)
    evidence: tuple[ReviewEvidence, ...] = Field(min_length=1, max_length=16)


class AgentReviewRecord(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_id: Literal["oh-goon.agent-review/v1"] = Field(
        default="oh-goon.agent-review/v1", alias="schema"
    )
    reviewer: str = Field(min_length=1, max_length=240)
    candidate_identity: str
    scope_identity: str
    skill_identity: str
    verdict: Literal["FINDING", "CLEAN", "LEAVE_ALONE", "INSUFFICIENT_EVIDENCE"]
    summary: str = Field(min_length=1, max_length=4000)
    reviewed_paths: tuple[str, ...] = Field(max_length=4096)
    findings: tuple[ReviewFinding, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_verdict(self) -> AgentReviewRecord:
        if (self.verdict == "FINDING") != bool(self.findings):
            raise ValueError("FINDING requires evidence; other verdicts cannot contain findings")
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding IDs must be unique within a reviewer occurrence")
        return self

    def verify(self, handoff: FrozenAgentStepHandoff, occurrence: str) -> None:
        handoff.verify()
        if (
            self.reviewer != occurrence
            or self.candidate_identity != handoff.source.repository.content_identity
            or self.scope_identity != review_scope(handoff)
            or self.skill_identity != handoff.source.skill.content_identity
        ):
            raise FleetPlanInvalid(
                "review claim does not match its candidate, scope, Skill and occurrence"
            )
        members = {"repo/" + member.path: member for member in handoff.source.repository.members}
        if len(set(self.reviewed_paths)) != len(self.reviewed_paths) or not set(
            self.reviewed_paths
        ).issubset(members):
            raise FleetPlanInvalid("review coverage contains duplicate or out-of-scope members")
        if self.verdict in ("CLEAN", "FINDING") and set(self.reviewed_paths) != set(members):
            raise FleetPlanInvalid("completed review must account for every captured scope member")
        for finding in self.findings:
            for evidence in finding.evidence:
                member = members.get(evidence.path)
                if member is None or member.sha256 != evidence.sha256:
                    raise FleetPlanInvalid("review evidence is outside the captured candidate")
                if evidence.line > max(1, len(member.data.splitlines())):
                    raise FleetPlanInvalid("review evidence line is outside the captured member")


def accept_review(text: str, handoff: FrozenAgentStepHandoff, occurrence: str) -> AgentReviewRecord:
    data = decode_agent_record(text)
    if data.get("schema") != "oh-goon.agent-review/v1":
        raise FleetPlanInvalid("Agent review must declare its record schema")
    try:
        result = AgentReviewRecord.model_validate(data)
    except ValidationError as exc:
        raise FleetPlanInvalid("Agent review has an invalid typed record") from exc
    result.verify(handoff, occurrence)
    return result


class FindingDisposition(ImmutableModel):
    reviewer: str
    finding_id: str
    decision: Literal["repair", "leave_alone"]
    rationale: str = Field(min_length=1, max_length=4000)


class FindingReference(ImmutableModel):
    reviewer: str
    finding_id: str


class RepairQueueEntry(ImmutableModel):
    queue_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    findings: tuple[FindingReference, ...] = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=4000)
    regression_expectation: str = Field(min_length=1, max_length=4000)


class AgentAdjudicationRecord(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_id: Literal["oh-goon.agent-adjudication/v1"] = Field(
        default="oh-goon.agent-adjudication/v1", alias="schema"
    )
    candidate_identity: str
    scope_identity: str
    dispositions: tuple[FindingDisposition, ...] = Field(min_length=1, max_length=128)
    queue: tuple[RepairQueueEntry, ...] = Field(max_length=128)

    def verify(self, reviews: tuple[AgentReviewRecord, ...]) -> None:
        expected = {(r.reviewer, f.finding_id) for r in reviews for f in r.findings}
        actual = [(d.reviewer, d.finding_id) for d in self.dispositions]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise FleetPlanInvalid("adjudication must account for every finding exactly once")
        admitted = {(d.reviewer, d.finding_id) for d in self.dispositions if d.decision == "repair"}
        grouped = [(ref.reviewer, ref.finding_id) for group in self.queue for ref in group.findings]
        if (
            len(grouped) != len(set(grouped))
            or set(grouped) != admitted
            or len({g.queue_id for g in self.queue}) != len(self.queue)
        ):
            raise FleetPlanInvalid("repair queue must partition admitted findings exactly once")
        if any(
            self.candidate_identity != r.candidate_identity
            or self.scope_identity != r.scope_identity
            for r in reviews
        ):
            raise FleetPlanInvalid("adjudication changed candidate or scope")


def accept_adjudication(
    text: str, reviews: tuple[AgentReviewRecord, ...]
) -> AgentAdjudicationRecord:
    data = decode_agent_record(text)
    if data.get("schema") != "oh-goon.agent-adjudication/v1":
        raise FleetPlanInvalid("Agent adjudication must declare its record schema")
    try:
        result = AgentAdjudicationRecord.model_validate(data)
    except ValidationError as exc:
        raise FleetPlanInvalid("Agent adjudication has an invalid typed record") from exc
    result.verify(reviews)
    return result
