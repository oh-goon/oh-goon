from __future__ import annotations

import socket
from dataclasses import dataclass
from ipaddress import IPv4Address

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedPlanStep
from oh_goon.core.models.fleet_plan_evidence import PlanHostnameResolutionEvidence
from oh_goon.core.models.fleet_plan_step_contract import (
    CidrAllowlistPlanEgress,
    CidrPlanEgressRule,
    HostnameAllowlistPlanEgress,
    PlanStepEgress,
)


@dataclass(frozen=True, slots=True)
class HostnameEgressBinding:
    """Frozen hostname answers plus the exact CIDR policy enforced for one step."""

    effective_policy: CidrAllowlistPlanEgress
    hosts: tuple[tuple[str, str], ...]


def capture_hostname_egress(
    plan: ResolvedFleetPlan | None,
) -> tuple[dict[str, HostnameEgressBinding], tuple[PlanHostnameResolutionEvidence, ...]]:
    if plan is None:
        return {}, ()
    hostname_policies = {
        step.step_id: step.capabilities.egress
        for step in plan.steps
        if isinstance(step.capabilities.egress, HostnameAllowlistPlanEgress)
    }
    hostnames = sorted(
        {rule.hostname for policy in hostname_policies.values() for rule in policy.rules}
    )
    resolved = {hostname: _resolve_ipv4(hostname) for hostname in hostnames}
    evidence = tuple(
        PlanHostnameResolutionEvidence(hostname=hostname, addresses=resolved[hostname])
        for hostname in hostnames
    )
    bindings: dict[str, HostnameEgressBinding] = {}
    for step_id, policy in hostname_policies.items():
        rules: dict[tuple[str, str, tuple[int, ...]], CidrPlanEgressRule] = {}
        host_entries: set[tuple[str, str]] = set()
        for rule in policy.rules:
            for address in resolved[rule.hostname]:
                host_entries.add((rule.hostname, address))
                effective = CidrPlanEgressRule(
                    cidr=f"{address}/32",
                    protocol=rule.protocol,
                    ports=rule.ports,
                )
                rules[(effective.cidr, effective.protocol, effective.ports)] = effective
        bindings[step_id] = HostnameEgressBinding(
            effective_policy=CidrAllowlistPlanEgress(
                rules=tuple(rules[key] for key in sorted(rules))
            ),
            hosts=tuple(sorted(host_entries)),
        )
    return bindings, evidence


def effective_egress_for(
    step: ResolvedPlanStep,
    bindings: dict[str, HostnameEgressBinding],
) -> PlanStepEgress:
    binding = bindings.get(step.step_id)
    return binding.effective_policy if binding is not None else step.capabilities.egress


def hosts_for(
    step: ResolvedPlanStep,
    bindings: dict[str, HostnameEgressBinding],
) -> tuple[tuple[str, str], ...]:
    binding = bindings.get(step.step_id)
    return binding.hosts if binding is not None else ()


def _resolve_ipv4(hostname: str) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(hostname, None, socket.AF_INET, 0, 0)
    except socket.gaierror as exc:
        raise FleetPlanInvalid(
            f"Fleet cannot resolve hostname egress destination {hostname!r}: {exc}"
        ) from exc
    addresses = {
        str(IPv4Address(str(sockaddr[0])))
        for _family, _type, _proto, _canonname, sockaddr in answers
    }
    if not addresses:
        raise FleetPlanInvalid(f"Fleet hostname egress destination has no IPv4 answers: {hostname}")
    return tuple(sorted(addresses, key=lambda value: int(IPv4Address(value))))
