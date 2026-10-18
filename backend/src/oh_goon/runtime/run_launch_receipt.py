from __future__ import annotations

from oh_goon.core.launch_manifest import LaunchFactState
from oh_goon.core.models.run_launch import (
    ApplicationLaunchReceipt,
    ManagedApplicationLaunchReceipt,
    UnmanagedApplicationLaunchReceipt,
)
from oh_goon.runtime.local_launch_context import LocalLaunchRuntimeContext
from oh_goon.runtime.local_launch_owner import BACKEND_SERVICE
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


def current_application_launch_receipt() -> ApplicationLaunchReceipt:
    """Capture the exact managed backend launch accepting a run, or explicit unmanaged state."""

    context = LocalLaunchRuntimeContext.from_environment()
    if context is None:
        return UnmanagedApplicationLaunchReceipt()
    manifest = LocalLaunchEventStore.configured().manifest(context.launch_id)
    if manifest.identity.owner_generation != context.owner_generation:
        raise ValueError("Fleet admission launch context belongs to another owner generation")
    backend = next(
        (service for service in manifest.observed.services if service.service == BACKEND_SERVICE),
        None,
    )
    if backend is None or backend.service_generation != context.backend_generation:
        raise ValueError("Fleet admission requires the exact observed backend launch generation")
    if backend.startup is None or backend.startup.value is not LaunchFactState.SATISFIED:
        raise ValueError("Fleet admission requires positive backend startup evidence")
    process = backend.process
    if process is None or process.birth_identity is None:
        raise ValueError("Fleet admission requires positive backend process-birth evidence")
    birth = process.birth_identity.value
    endpoint = backend.bound_endpoint
    return ManagedApplicationLaunchReceipt(
        launch_id=manifest.identity.launch_id,
        owner_generation=manifest.identity.owner_generation,
        backend_generation=context.backend_generation,
        profile=manifest.intent.profile,
        resolved_intent_identity=manifest.intent.identity(),
        backend_service_generation=backend.service_generation,
        backend_pid=birth.pid,
        backend_birth_provider=birth.provider,
        backend_boot_id=birth.boot_id,
        backend_start_ticks=birth.start_ticks,
        observed_host=endpoint.host.value if endpoint is not None else None,
        observed_port=endpoint.port.value if endpoint is not None else None,
    )
