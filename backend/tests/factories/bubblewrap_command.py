from __future__ import annotations

from types import SimpleNamespace

from oh_goon.integrations._fleet_plan_sandbox_launch import sandbox_process_launch
from oh_goon.integrations.fleet_plan_atomic_runtime_authority import bind_atomic_runtime_authority
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest


def prepared_bubblewrap_command(
    backend: BubblewrapProcessIsolation,
    request: IsolatedProcessRequest,
):
    launch = sandbox_process_launch(request)
    with bind_atomic_runtime_authority(request, launch) as atomic:
        argv, env = backend._command(
            request,
            launch=launch,
            atomic_authority=atomic,
            network_prefix=(),
        )
    return SimpleNamespace(argv=argv, cwd=None, env=env)
