from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from oh_goon.core.launch_manifest import LaunchLifecycleState, LaunchManifest
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth

LAUNCH_STATUS_SCHEMA = "oh-goon.launch-status/v1"


class LaunchObservationFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"


class LaunchOwnerProof(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True, slots=True)
class SafeLaunchStatus:
    """Credential-free public projection of one exact bound Launch Manifest."""

    launch_id: str
    owner_generation: int
    workspace: str
    profile: str
    lifecycle: LaunchLifecycleState
    observation_freshness: LaunchObservationFreshness
    owner_proof: LaunchOwnerProof
    services: tuple[dict[str, object], ...]
    failure: dict[str, object] | None
    schema: str = LAUNCH_STATUS_SCHEMA

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "launch_id": self.launch_id,
            "owner_generation": self.owner_generation,
            "workspace": self.workspace,
            "profile": self.profile,
            "lifecycle": self.lifecycle.value,
            "observation_freshness": self.observation_freshness.value,
            "owner_proof": self.owner_proof.value,
            "services": list(self.services),
            "failure": self.failure,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))


def safe_launch_status(
    manifest: LaunchManifest,
    *,
    process_observer: Callable[[int], ProcessBirthIdentity | None] = observe_process_birth,
) -> SafeLaunchStatus:
    """Whitelist safe fields and prove the exact owner process when possible.

    Never serialize the raw manifest or launch commands. Process absence or birth
    mismatch never changes lifecycle. It only makes the
    observation stale and owner proof unavailable.
    """

    terminal = manifest.lifecycle.is_terminal
    services: list[dict[str, object]] = []
    requested = {service.service: service for service in manifest.intent.services}
    observed = {service.service: service for service in manifest.observed.services}
    for name in sorted(requested):
        actual = observed.get(name)
        endpoint = None
        if actual is not None and actual.bound_endpoint is not None:
            endpoint = {
                "host": actual.bound_endpoint.host.value,
                "port": actual.bound_endpoint.port.value,
            }
        services.append(
            {
                "service": name,
                "required": requested[name].required,
                "service_generation": None if actual is None else actual.service_generation,
                "startup": "unknown" if actual is None else actual.state("startup").value,
                "readiness": "unknown" if actual is None else actual.state("readiness").value,
                "health": "unknown" if actual is None else actual.state("health").value,
                "bound_endpoint": endpoint,
            }
        )
    failure = None
    if manifest.failure is not None:
        failure = {
            "kind": manifest.failure.kind,
            "code": manifest.failure.code,
            "owner": manifest.failure.owner,
            "reason": manifest.failure.reason,
            "retryable": manifest.failure.retryable,
            "next_action": manifest.failure.next_action,
        }
    owner_proof = LaunchOwnerProof.NOT_REQUIRED if terminal else LaunchOwnerProof.UNAVAILABLE
    freshness = LaunchObservationFreshness.CURRENT if terminal else LaunchObservationFreshness.STALE
    if not terminal:
        owner = manifest.observed.owner_process
        if owner is not None and owner.birth_identity is not None:
            expected = owner.birth_identity.value
            observed_birth = process_observer(expected.pid)
            if observed_birth == expected:
                owner_proof = LaunchOwnerProof.AVAILABLE
                freshness = LaunchObservationFreshness.CURRENT

    return SafeLaunchStatus(
        launch_id=manifest.identity.launch_id,
        owner_generation=manifest.identity.owner_generation,
        workspace=manifest.identity.workspace,
        profile=manifest.intent.profile,
        lifecycle=manifest.lifecycle.state,
        observation_freshness=freshness,
        owner_proof=owner_proof,
        services=tuple(services),
        failure=failure,
    )
