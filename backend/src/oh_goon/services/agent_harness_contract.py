"""SKILL-3A: harness-neutral input and untrusted result for the original Agent Step.

This module is a pure adapter contract, not a runner, authority issuer, model
client, source loader, or journal. The original Fleet Step owner must still
supply Enforcer/LACE admission, physical custody and disclosure authorization
before an external harness can consume an input or produce effects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff, FrozenSourceMember
from oh_goon.core.product_identity import stable_id

_MAX_RESULT_BYTES = 1024 * 1024
_MAX_FINDINGS = 128
_MAX_FINDING_CHARS = 4000
_RESULT_SCHEMA = "oh-goon.agent-harness-submission/v1"


@dataclass(frozen=True, slots=True)
class AgentHarnessInput:
    """Exact AG-02 source-backed input for a future admitted harness.

    This retains references to the original frozen members: no filesystem walk,
    git operation, base64 re-packetization, or alternate source identity.
    Producing this value is NOT permission to disclose it to a model/recipient.
    """

    handoff: FrozenAgentStepHandoff = field(repr=False)

    @property
    def handoff_identity(self) -> str:
        return self.handoff.handoff_identity

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
            raise FleetPlanInvalid("SKILL-3A SKILL.md must be UTF-8 for a text harness") from exc
        if not instructions.strip():
            raise FleetPlanInvalid("SKILL-3A SKILL.md must contain instructions")
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
    def requested_effects(self):
        """Original authored permission demands; NEVER a tool/egress grant."""
        return self.handoff.requested_effects


def prepare_agent_harness_input(
    packet: bytes, *, expected_handoff_identity: str,
) -> AgentHarnessInput:
    """Verify the original wire packet once; never reacquire live source paths.

    The returned object is still PREWORK_NOT_ADMITTED. The caller may inspect
    it locally but must not send it to an LLM or launch a tool absent separate
    original Enforcer/LACE/disclosure authority.
    """
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=expected_handoff_identity,
    )
    result = AgentHarnessInput(handoff=handoff)
    _ = result.skill_instructions
    return result


@dataclass(frozen=True, slots=True)
class AgentHarnessFinding:
    """A *claim* by the untrusted harness, not independent evidence."""

    location: str
    explanation: str


@dataclass(frozen=True, slots=True)
class AgentHarnessSubmission:
    """Untrusted model result. Never classifies Run/Step success or CLEAN.

    Game Tape and the original Step executor must use independently verified
    execution/evidence outcomes; this only preserves a bounded candidate report.
    """

    handoff_identity: str
    verdict: Literal["CLEAN", "FINDINGS", "INCONCLUSIVE"]
    findings: tuple[AgentHarnessFinding, ...]
    summary: str
    authority: Literal["UNTRUSTED_HARNESS_CLAIM"] = "UNTRUSTED_HARNESS_CLAIM"

    @property
    def submission_identity(self) -> str:
        return stable_id("agent-harness-submission-v1", {
            "handoff_identity": self.handoff_identity,
            "verdict": self.verdict,
            "findings": [
                {"location": f.location, "explanation": f.explanation} for f in self.findings
            ],
            "summary": self.summary,
        })


def decode_agent_harness_submission(
    payload: bytes, *, expected_handoff_identity: str,
) -> AgentHarnessSubmission:
    """Parse a bounded harness report, tied to the original frozen Step.

    Neither a CLEAN verdict nor a tool-use claim constitutes Enforcer/LACE
    authorization or verification. Extra fields are rejected rather than
    giving an LLM room to smuggle authority/permissions into a result.
    """
    if type(payload) is not bytes or len(payload) > _MAX_RESULT_BYTES:
        raise FleetPlanInvalid("SKILL-3A harness submission must be bounded bytes")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise FleetPlanInvalid("SKILL-3A duplicate harness submission key")
            result[name] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8", "strict"), object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                FleetPlanInvalid("SKILL-3A invalid nonfinite result value")
            ),
        )
    except (UnicodeError, ValueError, TypeError) as exc:
        raise FleetPlanInvalid("SKILL-3A invalid harness result encoding") from exc
    if (type(value) is not dict or set(value) != {
        "schema", "handoff_identity", "verdict", "findings", "summary"
    } or value["schema"] != _RESULT_SCHEMA or canonical_bytes(value) != payload):
        raise FleetPlanInvalid("SKILL-3A invalid harness result schema or canonical encoding")
    if (type(expected_handoff_identity) is not str
            or not expected_handoff_identity.startswith("agent-step-prework-handoff-v1-")
            or len(expected_handoff_identity) != len("agent-step-prework-handoff-v1-") + 64
            or value["handoff_identity"] != expected_handoff_identity):
        raise FleetPlanInvalid("SKILL-3A harness submission changed its Step identity")
    if (value["verdict"] not in ("CLEAN", "FINDINGS", "INCONCLUSIVE")
            or type(value["verdict"]) is not str
            or type(value["summary"]) is not str
            or not value["summary"].strip()
            or len(value["summary"]) > _MAX_FINDING_CHARS
            or type(value["findings"]) is not list
            or len(value["findings"]) > _MAX_FINDINGS):
        raise FleetPlanInvalid("SKILL-3A invalid harness verdict or report bounds")
    findings = []
    for raw in value["findings"]:
        if type(raw) is not dict or set(raw) != {"location", "explanation"}:
            raise FleetPlanInvalid("SKILL-3A invalid finding schema")
        if any(type(raw[k]) is not str or not raw[k].strip()
               or len(raw[k]) > _MAX_FINDING_CHARS for k in ("location", "explanation")):
            raise FleetPlanInvalid("SKILL-3A invalid finding fields")
        findings.append(AgentHarnessFinding(raw["location"], raw["explanation"]))
    if value["verdict"] == "CLEAN" and findings:
        raise FleetPlanInvalid("SKILL-3A CLEAN cannot carry findings")
    if value["verdict"] == "FINDINGS" and not findings:
        raise FleetPlanInvalid("SKILL-3A FINDINGS requires a finding")
    return AgentHarnessSubmission(
        handoff_identity=expected_handoff_identity, verdict=value["verdict"],
        findings=tuple(findings), summary=value["summary"],
    )
