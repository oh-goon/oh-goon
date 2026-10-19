from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass

from oh_goon.core.launch_manifest import LaunchFactState, LaunchLifecycleState, LaunchManifest
from oh_goon.core.launch_status import LaunchOwnerProof, safe_launch_status
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth

NAVIGATION_SERVICE_BY_PROFILE = {
    "built-local": "backend",
    "contributor-dev": "frontend",
}


@dataclass(frozen=True, slots=True)
class LaunchNavigationTarget:
    launch_id: str
    service: str
    endpoint: str


def safe_launch_navigation_target(
    manifest: LaunchManifest,
    *,
    process_observer: Callable[[int], ProcessBirthIdentity | None] = observe_process_birth,
) -> LaunchNavigationTarget:
    """Resolve one browser target from positive observed launch truth only.

    Configured endpoint intent never supplies host/port here. It contributes only the
    declared local-exposure policy for the selected product profile. A target requires
    a positively proven current owner, authoritative launch readiness/degradation, a
    ready service generation, and an observed loopback binding.
    """

    status = safe_launch_status(manifest, process_observer=process_observer)
    if status.owner_proof is not LaunchOwnerProof.AVAILABLE:
        raise ValueError("current launch owner cannot be positively proven")
    if manifest.lifecycle.state not in {LaunchLifecycleState.READY, LaunchLifecycleState.DEGRADED}:
        raise ValueError("current launch is not ready for navigation")

    service_name = NAVIGATION_SERVICE_BY_PROFILE.get(manifest.intent.profile)
    if service_name is None:
        raise ValueError(
            f"launch profile has no browser navigation policy: {manifest.intent.profile}"
        )
    requested = next(
        (item for item in manifest.intent.services if item.service == service_name), None
    )
    if requested is None or requested.endpoint is None or requested.endpoint.exposure != "local":
        raise ValueError("launch navigation service lacks explicit local exposure intent")
    observed = next(
        (item for item in manifest.observed.services if item.service == service_name), None
    )
    if observed is None or observed.readiness is None:
        raise ValueError("launch navigation service has no authoritative readiness observation")
    if observed.readiness.value is not LaunchFactState.SATISFIED:
        raise ValueError("launch navigation service is not ready")
    if observed.bound_endpoint is None:
        raise ValueError("launch navigation service has no observed bound endpoint")

    host = observed.bound_endpoint.host.value
    port = observed.bound_endpoint.port.value
    if not _is_loopback_host(host):
        raise ValueError("observed launch navigation endpoint is not loopback-only")
    rendered_host = f"[{host}]" if ":" in host else host
    return LaunchNavigationTarget(
        launch_id=manifest.identity.launch_id,
        service=service_name,
        endpoint=f"http://{rendered_host}:{port}",
    )


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
