"""Passive input and untrusted submission contract for replaceable Agent harnesses.

This is the existing harness contract, moved to core so execution integrations
can consume it without importing services. It performs no discovery, launch,
disclosure, admission, result publication or native configuration parsing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal, get_args

from pydantic import Field, field_validator, model_validator

from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_review import MAX_REVIEW_BYTES, review_scope
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff, FrozenSourceMember
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_observation import AgentHarnessObservation as AgentHarnessObservation
from oh_goon.core.models.fleet_plan import AgentResultRole, AgentStepRequestedEffects
from oh_goon.core.models.fleet_plan_evidence import PlanContentObjectEvidence
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits, PlanExecutionPolicy
from oh_goon.core.product_identity import stable_id

_MAX_RESULT_BYTES = 1024 * 1024
_MAX_FINDINGS = 128
_MAX_FINDING_CHARS = 4000

HarnessRole = AgentResultRole
HarnessCapability = Literal[
    "frozen_source",
    "hashmarks_evidence",
    "report",
    "review",
    "adjudication",
    "proposal",
    "observations",
    "artifacts",
    "terminal",
]
_CAPABILITIES = frozenset(get_args(HarnessCapability))


def _capability_names(value: tuple[str, ...]) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or any(type(item) is not str or item not in _CAPABILITIES for item in value)
        or value != tuple(sorted(set(value)))
    ):
        raise FleetPlanInvalid("Agent harness capabilities must be known, unique and sorted")
    return value


class AgentHarnessSelection(ImmutableModel):
    """Caller-resolved implementation declaration, never installed/admitted proof.

    Native model settings remain native. An observable nonsecret configuration
    identity may be carried opaquely; absence stays unknown rather than guessed.
    """

    harness: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    implementation_identity: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,159}$")
    native_configuration_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    capabilities: tuple[HarnessCapability, ...] = Field(max_length=9)

    @field_validator("capabilities")
    @classmethod
    def canonical_capabilities(
        cls, value: tuple[HarnessCapability, ...]
    ) -> tuple[HarnessCapability, ...]:
        try:
            _capability_names(value)
        except FleetPlanInvalid as exc:
            raise ValueError(str(exc)) from exc
        return value


class AgentHarnessIntent(ImmutableModel):
    """Transport original resolved execution/disclosure intent without admitting it.

    The original Fleet/Step owner verifies these facts against its actual
    execution. Backend selection, permissions, deadlines and cancellation keep
    their existing owners; no native agent may reinterpret this as a grant.
    """

    execution_identity: ExecutionIdentity
    backend_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    policy: PlanExecutionPolicy
    limits: FleetPlanRunLimits | None = None
    disclosure_recipients: tuple[str, ...] = Field(default=(), max_length=16)


class AgentHarnessOutputLimits(ImmutableModel):
    max_submission_bytes: int = Field(
        default=_MAX_RESULT_BYTES, strict=True, ge=1, le=_MAX_RESULT_BYTES
    )
    max_observations: int = Field(default=128, strict=True, ge=0, le=128)
    max_artifacts: int = Field(default=32, strict=True, ge=0, le=32)
    max_artifact_bytes: int = Field(
        default=64 * 1024 * 1024, strict=True, ge=0, le=64 * 1024 * 1024
    )


@dataclass(frozen=True, slots=True)
class AgentHarnessInput:
    """Exact frozen source plus explicitly selected, non-authorizing intent.

    Required support rejects during preparation, before a Step or disclosure.
    Optional support is the requested/supported intersection, without fallback.
    Hashmarks evidence delivery remains mandatory before reasoning; a descriptor
    is not evidence that a native implementation actually delivers it.
    """

    handoff: FrozenAgentStepHandoff = field(repr=False)
    selection: AgentHarnessSelection
    execution: AgentHarnessIntent
    occurrence: str
    role: HarnessRole = "report"
    required_capabilities: tuple[HarnessCapability, ...] = ()
    optional_capabilities: tuple[HarnessCapability, ...] = ()
    output_limits: AgentHarnessOutputLimits = field(default_factory=AgentHarnessOutputLimits)

    def __post_init__(self) -> None:
        if (
            type(self.handoff) is not FrozenAgentStepHandoff
            or type(self.selection) is not AgentHarnessSelection
            or type(self.execution) is not AgentHarnessIntent
            or type(self.output_limits) is not AgentHarnessOutputLimits
        ):
            raise FleetPlanInvalid("Agent harness input requires frozen typed intent")
        self.handoff.verify()
        if self.selection.harness != self.handoff.harness:
            raise FleetPlanInvalid("Agent harness selection differs from the frozen Step")
        if (
            type(self.occurrence) is not str
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", self.occurrence) is None
            or self.role not in get_args(HarnessRole)
        ):
            raise FleetPlanInvalid("Agent harness occurrence or result role is invalid")
        required = _capability_names(self.required_capabilities)
        optional = _capability_names(self.optional_capabilities)
        if set(required) & set(optional):
            raise FleetPlanInvalid("Agent harness required and optional capabilities overlap")
        needed = {"frozen_source", "hashmarks_evidence", self.role, *required}
        missing = needed - set(self.selection.capabilities)
        if missing:
            raise FleetPlanInvalid(f"Agent harness lacks required capabilities: {sorted(missing)}")
        if self.execution.disclosure_recipients != self.requested_effects.recipients:
            raise FleetPlanInvalid(
                "Agent harness disclosure differs from the frozen recipient request"
            )

    @property
    def handoff_identity(self) -> str:
        return self.handoff.handoff_identity

    @property
    def input_identity(self) -> str:
        return stable_id(
            "agent-harness-input-v2",
            {
                "handoff_identity": self.handoff_identity,
                "selection": self.selection.model_dump(mode="json"),
                "execution": self.execution.model_dump(mode="json"),
                "occurrence": self.occurrence,
                "role": self.role,
                "required_capabilities": self.required_capabilities,
                "optional_capabilities": self.optional_capabilities,
                "output_limits": self.output_limits.model_dump(mode="json"),
            },
        )

    @property
    def enabled_capabilities(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {"frozen_source", "hashmarks_evidence", self.role, *self.required_capabilities}
                | (set(self.optional_capabilities) & set(self.selection.capabilities))
            )
        )

    @property
    def unavailable_optional_capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.optional_capabilities) - set(self.selection.capabilities)))

    @property
    def harness(self) -> str:
        return self.handoff.harness

    @property
    def task(self) -> str:
        return self.handoff.task

    @property
    def timeout_seconds(self) -> float:
        return self.handoff.timeout_seconds

    @property
    def skill_instructions(self) -> str:
        member = next(item for item in self.handoff.source.skill.members if item.path == "SKILL.md")
        try:
            instructions = member.data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise FleetPlanInvalid("Agent harness SKILL.md must be UTF-8") from exc
        if not instructions.strip():
            raise FleetPlanInvalid("Agent harness SKILL.md must contain instructions")
        return instructions

    @property
    def skill_resources(self) -> tuple[FrozenSourceMember, ...]:
        return self.handoff.source.skill.members

    @property
    def repository_resources(self) -> tuple[FrozenSourceMember, ...]:
        return self.handoff.source.repository.members

    @property
    def source_identity(self) -> str:
        return self.handoff.source.capture_identity

    @property
    def candidate_identity(self) -> str:
        return self.handoff.source.repository.content_identity

    @property
    def scope_identity(self) -> str:
        return review_scope(self.handoff)

    @property
    def skill_identity(self) -> str:
        return self.handoff.source.skill.content_identity

    @property
    def requested_effects(self) -> AgentStepRequestedEffects:
        """Original authored demands; never a tool, write or disclosure grant."""
        return self.handoff.requested_effects


def prepare_agent_harness_input(
    packet: bytes,
    *,
    expected_handoff_identity: str,
    selection: AgentHarnessSelection,
    execution: AgentHarnessIntent,
    occurrence: str,
    role: HarnessRole = "report",
    required_capabilities: tuple[HarnessCapability, ...] = (),
    optional_capabilities: tuple[HarnessCapability, ...] = (),
    output_limits: AgentHarnessOutputLimits | None = None,
) -> AgentHarnessInput:
    """Verify the existing delivery, then bind selected support before execution.

    Source paths are never reopened. The returned value is still prework, not
    execution/disclosure authority. The native Step receiver checks this input
    against original resolution/preflight and retains mandatory Hashmarks delivery.
    """
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=expected_handoff_identity
    )
    result = AgentHarnessInput(
        handoff=handoff,
        selection=selection,
        execution=execution,
        occurrence=occurrence,
        role=role,
        required_capabilities=required_capabilities,
        optional_capabilities=optional_capabilities,
        output_limits=AgentHarnessOutputLimits() if output_limits is None else output_limits,
    )
    _ = result.skill_instructions
    return result


class AgentHarnessFinding(ImmutableModel):
    """A generic report claim, never a source-qualified review finding."""

    location: str = Field(min_length=1, max_length=_MAX_FINDING_CHARS)
    explanation: str = Field(min_length=1, max_length=_MAX_FINDING_CHARS)

    @model_validator(mode="after")
    def nonblank_fields(self) -> AgentHarnessFinding:
        if not self.location.strip() or not self.explanation.strip():
            raise ValueError("Agent harness finding fields must be nonblank")
        return self


class AgentHarnessArtifactReference(ImmutableModel):
    """Claim about existing content identity, not a path, URL or retention grant.

    Original content owners must establish existence, digest, size and retention
    independently before any reference is consumed or promoted.
    """

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    content: PlanContentObjectEvidence


class AgentHarnessSubmission(ImmutableModel):
    """Untrusted protocol result; COMPLETED does not classify physical success.

    Role records remain opaque here. The original review, adjudication or patch
    acceptor must validate their source/occurrence/accounting/effect semantics.
    No generic verdict or event establishes verification, admission or custody.
    """

    schema_id: Literal["oh-goon.agent-harness-submission/v2"] = Field(alias="schema")
    input_identity: str
    handoff_identity: str
    status: Literal["COMPLETED", "FAILED", "INCOMPLETE"]
    verdict: Literal["CLEAN", "FINDINGS", "INCONCLUSIVE"] | None
    findings: tuple[AgentHarnessFinding, ...] = Field(max_length=_MAX_FINDINGS)
    summary: str = Field(min_length=1, max_length=_MAX_FINDING_CHARS)
    record: str | None
    observations: tuple[AgentHarnessObservation, ...] = Field(max_length=128)
    artifacts: tuple[AgentHarnessArtifactReference, ...] = Field(max_length=32)

    @property
    def authority(self) -> Literal["UNTRUSTED_HARNESS_CLAIM"]:
        return "UNTRUSTED_HARNESS_CLAIM"

    @property
    def submission_identity(self) -> str:
        return stable_id(
            "agent-harness-submission-v2",
            self.model_dump(mode="json", by_alias=True, exclude_computed_fields=True),
        )


def decode_agent_harness_submission(
    payload: bytes,
    *,
    expected_input: AgentHarnessInput,
) -> AgentHarnessSubmission:
    """Decode one bounded claim for the exact prepared input, without effects.

    Native failure/cancellation/timeout and verification remain authoritative.
    Incomplete/failed reports cannot carry a completed verdict or role payload.
    """
    if type(expected_input) is not AgentHarnessInput:
        raise FleetPlanInvalid("Agent harness submission requires its prepared input")
    if (
        type(payload) is not bytes
        or len(payload) > expected_input.output_limits.max_submission_bytes
    ):
        raise FleetPlanInvalid("Agent harness submission must be bounded bytes")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise FleetPlanInvalid("Agent harness submission has duplicate keys")
            result[name] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                FleetPlanInvalid("Agent harness submission has nonfinite values")
            ),
        )
        result = AgentHarnessSubmission.model_validate(value)
        # This also rejects omitted fields and coercion of native content fields.
        if (
            canonical_bytes(
                result.model_dump(mode="json", by_alias=True, exclude_computed_fields=True)
            )
            != payload
        ):
            raise FleetPlanInvalid("Agent harness submission must use exact canonical encoding")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise FleetPlanInvalid("Invalid Agent harness submission encoding or schema") from exc
    if (
        result.input_identity != expected_input.input_identity
        or result.handoff_identity != expected_input.handoff_identity
    ):
        raise FleetPlanInvalid("Agent harness submission changed its prepared input identity")
    if not result.summary.strip():
        raise FleetPlanInvalid("Agent harness summary must be nonblank")
    if result.status != "COMPLETED":
        if result.verdict is not None or result.findings or result.record is not None:
            raise FleetPlanInvalid("Failed/incomplete harness submission cannot claim completion")
    elif expected_input.role == "report":
        if result.record is not None or result.verdict is None:
            raise FleetPlanInvalid("Generic harness report requires its bounded verdict")
        if (result.verdict == "FINDINGS") != bool(result.findings):
            # INCONCLUSIVE historically permits partial findings.
            if result.verdict != "INCONCLUSIVE":
                raise FleetPlanInvalid("Agent harness verdict and findings disagree")
    elif (
        result.verdict is not None
        or result.findings
        or result.record is None
        or not result.record.strip()
        or len(result.record.encode("utf-8")) > MAX_REVIEW_BYTES
    ):
        raise FleetPlanInvalid(
            "Agent harness phase requires a bounded role record, not a generic verdict"
        )
    limits = expected_input.output_limits
    if (
        len(result.observations) > limits.max_observations
        or len(result.artifacts) > limits.max_artifacts
    ):
        raise FleetPlanInvalid("Agent harness observations or artifacts exceed requested bounds")
    if result.observations and "observations" not in expected_input.enabled_capabilities:
        raise FleetPlanInvalid("Agent harness observations were not enabled")
    if result.artifacts and "artifacts" not in expected_input.enabled_capabilities:
        raise FleetPlanInvalid("Agent harness artifacts were not enabled")
    if len({item.name for item in result.artifacts}) != len(result.artifacts):
        raise FleetPlanInvalid("Agent harness artifact names must be unique")
    if sum(item.content.byte_count for item in result.artifacts) > limits.max_artifact_bytes:
        raise FleetPlanInvalid("Agent harness artifact claims exceed their aggregate byte budget")
    return result
