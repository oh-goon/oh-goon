from __future__ import annotations

import re
from ipaddress import ip_address, ip_network
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel


class RepositoryWorkspaceSource(ImmutableModel):
    """Start the step from the repository state admitted for this plan execution."""

    kind: Literal["repository"] = "repository"


class StepWorkspaceSource(ImmutableModel):
    """Start the step from one explicitly published earlier step workspace."""

    kind: Literal["step"] = "step"
    step_id: str = Field(min_length=1, max_length=80)


PlanWorkspaceSource = Annotated[
    RepositoryWorkspaceSource | StepWorkspaceSource,
    Field(discriminator="kind"),
]


class PlanStepWorkspace(ImmutableModel):
    """Typed filesystem dataflow for one step.

    Every strict CI/CD attempt starts from a private copy of ``source``.  The copy is
    never visible to another step unless ``publish`` is true and the consumer names
    this step through ``StepWorkspaceSource``.
    """

    source: PlanWorkspaceSource = Field(default_factory=RepositoryWorkspaceSource)
    publish: bool = False


class EmptyPlanCacheSource(ImmutableModel):
    """Start the step with a fresh private cache tree."""

    kind: Literal["empty"] = "empty"


class StepPlanCacheSource(ImmutableModel):
    """Start from one explicitly published cache tree of an earlier step."""

    kind: Literal["step"] = "step"
    step_id: str = Field(min_length=1, max_length=80)



class RetainedPlanCacheSource(ImmutableModel):
    """Start strict CI/CD from one repository-owned immutable cache reference.

    ``private_copy`` preserves writable-cache seed semantics inside the strict
    attempt-private execution boundary. ``read_only`` is an explicit zero-copy
    strict execution input: Fleet verifies the immutable retained object and mounts
    those exact bytes read-only at ``/cache``. Direct/verification Plans cannot use
    retained cross-run cache authority because they do not freeze retained inputs.
    """

    kind: Literal["retained"] = "retained"
    reference_id: str = Field(min_length=1, max_length=96)
    access: Literal["private_copy", "read_only"] = "private_copy"

    @field_validator("reference_id")
    @classmethod
    def validate_reference_or_input(cls, value: str) -> str:
        if re.fullmatch(r"ref-[0-9a-f]{32}", value):
            return value
        if re.fullmatch(r"\$\{\{ inputs\.[A-Za-z_][A-Za-z0-9_-]{0,63} \}\}", value):
            return value
        raise ValueError("retained cache reference must be a ref-* identity or one direct input projection")


PlanCacheSource = Annotated[
    EmptyPlanCacheSource | StepPlanCacheSource | RetainedPlanCacheSource,
    Field(discriminator="kind"),
]


class PlanStepCache(ImmutableModel):
    """Explicit cache lineage; retries never inherit failed-attempt cache mutations."""

    source: PlanCacheSource = Field(default_factory=EmptyPlanCacheSource)
    publish: bool = False
    retain: bool = False

    @model_validator(mode="after")
    def validate_retention(self) -> PlanStepCache:
        if self.retain and not self.publish:
            raise ValueError("Fleet cache retain=true requires publish=true")
        if (
            isinstance(self.source, RetainedPlanCacheSource)
            and self.source.access == "read_only"
            and (self.publish or self.retain)
        ):
            raise ValueError("Fleet read-only retained cache input cannot be published or retained")
        return self


class DeniedPlanEgress(ImmutableModel):
    """No IP network namespace access is granted to the step."""

    mode: Literal["none"] = "none"


class CidrPlanEgressRule(ImmutableModel):
    """One kernel-enforceable IPv4 destination rule for a strict CI/CD process."""

    cidr: str = Field(min_length=3, max_length=43)
    protocol: Literal["tcp", "udp"]
    ports: tuple[int, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_rule(self) -> CidrPlanEgressRule:
        try:
            network = ip_network(self.cidr, strict=False)
        except ValueError as exc:
            raise ValueError(f"Fleet egress rule has invalid CIDR {self.cidr!r}") from exc
        if network.version != 4:
            raise ValueError("Fleet destination-enforced egress currently supports IPv4 CIDRs only")
        if str(network) != self.cidr:
            raise ValueError(
                f"Fleet egress CIDR must use canonical network form {network}, not {self.cidr!r}"
            )
        if len(self.ports) != len(set(self.ports)):
            raise ValueError("Fleet egress rule ports must be unique")
        if any(port < 1 or port > 65535 for port in self.ports):
            raise ValueError("Fleet egress rule ports must be between 1 and 65535")
        return self


class CidrAllowlistPlanEgress(ImmutableModel):
    """Grant only explicitly admitted IPv4 CIDR/protocol/port destinations."""

    mode: Literal["cidr_allowlist"] = "cidr_allowlist"
    rules: tuple[CidrPlanEgressRule, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_rules(self) -> CidrAllowlistPlanEgress:
        keys = [(rule.cidr, rule.protocol, rule.ports) for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("Fleet egress allowlist rules must be unique")
        return self


_TOOL_CAPABILITY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class HostnamePlanEgressRule(ImmutableModel):
    """One hostname whose IPv4 answers are frozen before EFFECT_STARTED."""

    hostname: str = Field(min_length=3, max_length=253)
    protocol: Literal["tcp", "udp"]
    ports: tuple[int, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_rule(self) -> HostnamePlanEgressRule:
        if self.hostname != self.hostname.lower() or self.hostname.endswith("."):
            raise ValueError(
                "Fleet egress hostname must use canonical lowercase form without a trailing dot"
            )
        try:
            ip_address(self.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("Fleet hostname egress requires a DNS name, not an IP literal")
        if not _HOSTNAME.fullmatch(self.hostname):
            raise ValueError(
                f"Fleet egress hostname is not a canonical DNS name: {self.hostname!r}"
            )
        if len(self.ports) != len(set(self.ports)):
            raise ValueError("Fleet hostname egress rule ports must be unique")
        if any(port < 1 or port > 65535 for port in self.ports):
            raise ValueError("Fleet hostname egress rule ports must be between 1 and 65535")
        return self


class HostnameAllowlistPlanEgress(ImmutableModel):
    """Resolve named destinations once, then enforce only those bound IPv4 answers."""

    mode: Literal["hostname_allowlist"] = "hostname_allowlist"
    rules: tuple[HostnamePlanEgressRule, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_rules(self) -> HostnameAllowlistPlanEgress:
        keys = [(rule.hostname, rule.protocol, rule.ports) for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("Fleet hostname egress allowlist rules must be unique")
        return self


class UnrestrictedHostPlanEgress(ImmutableModel):
    """Grant the step the host network namespace without destination filtering."""

    mode: Literal["host_unrestricted"] = "host_unrestricted"


PlanStepEgress = Annotated[
    DeniedPlanEgress
    | CidrAllowlistPlanEgress
    | HostnameAllowlistPlanEgress
    | UnrestrictedHostPlanEgress,
    Field(discriminator="mode"),
]


class PlanStepCapabilities(ImmutableModel):
    """Explicit external process capabilities beyond the Step's primary executable.

    ``tools`` names additional execution-environment programs that repository-owned
    command delegation may invoke as children. They are resolved and frozen by the
    same strict host-path authority as the primary executable; ambient PATH remains
    unavailable inside the sandbox.
    """

    egress: PlanStepEgress = Field(default_factory=DeniedPlanEgress)
    tools: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    nested_strict_execution: bool = False

    @model_validator(mode="after")
    def validate_tools(self) -> PlanStepCapabilities:
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("Fleet Step capability tool names must be unique")
        if self.tools != tuple(sorted(self.tools)):
            raise ValueError("Fleet Step capability tool names must use canonical sorted order")
        for name in self.tools:
            if _TOOL_CAPABILITY_NAME.fullmatch(name) is None:
                raise ValueError(f"Invalid Fleet Step capability tool name: {name!r}")
        return self


def compile_simple_network_alias(value):
    """Compile the friendly ``network: bool`` authoring form to canonical egress.

    ``network: false`` is the normal default and therefore normally omitted.
    Advanced destination policies keep using ``capabilities.egress``. The two forms
    are mutually exclusive so there is never precedence or merge behavior.
    """
    if not isinstance(value, dict) or "network" not in value:
        return value
    payload = dict(value)
    enabled = payload.pop("network")
    if type(enabled) is not bool:
        raise ValueError("Fleet step network must be true or false")
    if "capabilities" in payload:
        raise ValueError(
            "Fleet step must choose either network or advanced capabilities, never both"
        )
    payload["capabilities"] = {"egress": {"mode": "host_unrestricted" if enabled else "none"}}
    return payload
