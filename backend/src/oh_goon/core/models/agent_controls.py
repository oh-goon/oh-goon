"""Passive Run control availability, separate from native qualification."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel

MAX_AGENT_CONTROL_SELECTIONS = 8
MAX_AGENT_CONTROL_REASON_CHARS = 512
MAX_AGENT_CONTROL_ID_CHARS = 512

AgentControlAction = Literal[
    "fresh_handoff",
    "exact_native_resume",
    "cross_harness_fresh_handoff",
    "steer",
    "follow_up",
    "abort",
    "detach",
]
AgentControlState = Literal["available", "unsupported", "unqualified"]
AgentControlRequirement = Literal["fresh_resolution", "fresh_admission", "original_run_owner"]
NativeAgentControlMode = Literal[
    "headless_cli", "interactive_cli", "codex_login_cli", "codex_service", "pi_rpc"
]
AgentControlPlacementAvailability = Literal["recorded", "unavailable", "omitted_over_limit"]


class AgentControlSelection(ImmutableModel):
    """Exact frozen selection; neither inventory nor installed capability proof."""

    step_id: str = Field(min_length=1, max_length=80)
    harness: Literal["native"] = "native"
    agent: Literal["codex", "opencode", "pi"]
    mode: NativeAgentControlMode
    implementation_identity: str = Field(min_length=1, max_length=160)
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile: str = Field(min_length=1, max_length=64)
    selected_backend: Literal["local", "bubblewrap"]
    sandbox_owner: Literal["harness", "execution_backend"]
    selection_availability: Literal["frozen_intent"] = "frozen_intent"
    placement_backend_id: str | None = Field(default=None, max_length=80)
    placement_configuration_id: str | None = Field(default=None, max_length=160)
    placement_availability: AgentControlPlacementAvailability
    environment_qualification: Literal["unqualified"] = "unqualified"

    @model_validator(mode="after")
    def validate_placement_availability(self) -> AgentControlSelection:
        complete = (
            self.placement_backend_id is not None and self.placement_configuration_id is not None
        )
        absent = self.placement_backend_id is None and self.placement_configuration_id is None
        if (self.placement_availability == "recorded" and not complete) or (
            self.placement_availability != "recorded" and not absent
        ):
            raise ValueError("Agent control placement identity must match its availability")
        return self


class AgentRunControl(ImmutableModel):
    """Product route availability, never permission to invoke that route."""

    action: AgentControlAction
    state: AgentControlState
    reason: str = Field(min_length=1, max_length=MAX_AGENT_CONTROL_REASON_CHARS)
    requirements: tuple[AgentControlRequirement, ...] = Field(default=(), max_length=3)
    route: Literal["fresh_run_with_handoff", "cancel_run"] | None = None

    @model_validator(mode="after")
    def validate_route(self) -> AgentRunControl:
        if self.state == "available" and self.route is None:
            raise ValueError("Available Agent controls require their existing product route")
        if self.state != "available" and self.route is not None:
            raise ValueError("Unavailable Agent controls cannot expose an execution route")
        if self.route == "cancel_run" and self.action != "abort":
            raise ValueError("Only abort can refer to original Run cancellation")
        if self.route == "fresh_run_with_handoff" and self.action not in {
            "fresh_handoff",
            "cross_harness_fresh_handoff",
        }:
            raise ValueError("Fresh handoff routes cannot resume or steer native sessions")
        return self


class AgentRunControls(ImmutableModel):
    """Bounded control projection from one immutable canonical Run read cut."""

    fleet_run_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_AGENT_CONTROL_ID_CHARS
    )
    fleet_run_id_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_id_availability: Literal["recorded", "omitted_over_limit"] = "recorded"
    journal_revision: int | None = Field(default=None, ge=0)
    selections: tuple[AgentControlSelection, ...] = Field(
        default=(), max_length=MAX_AGENT_CONTROL_SELECTIONS
    )
    omitted_selections: int = Field(default=0, ge=0)
    controls: tuple[AgentRunControl, ...] = Field(min_length=7, max_length=7)
    environment_qualification: Literal["unqualified"] = "unqualified"
    qualification_reason: str = Field(min_length=1, max_length=MAX_AGENT_CONTROL_REASON_CHARS)

    @model_validator(mode="after")
    def validate_control_membership(self) -> AgentRunControls:
        if (self.run_id_availability == "recorded") != (self.fleet_run_id is not None) or (
            self.run_id_availability == "omitted_over_limit" and self.fleet_run_id_sha256 is None
        ):
            raise ValueError("Agent control Run identity must retain its availability")
        expected = {
            "fresh_handoff",
            "exact_native_resume",
            "cross_harness_fresh_handoff",
            "steer",
            "follow_up",
            "abort",
            "detach",
        }
        if {control.action for control in self.controls} != expected:
            raise ValueError("Agent Run controls require each distinct action exactly once")
        return self
