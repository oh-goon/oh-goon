from __future__ import annotations

from oh_goon.core.models.policy import PolicySnapshot, PolicySources, PolicyValues
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import PolicySource, WorkspaceMode, WorkspaceScope


def make_policy_snapshot() -> PolicySnapshot:
    values = PolicyValues(
        require_remote=True,
        test_command="uv run --frozen pytest",
        require_audit=False,
        fail_on_audit=True,
        audit_timeout_seconds=20,
        git_timeout_seconds=30,
        malware_check=False,
        workspace_mode=WorkspaceMode.ROOT,
        workspace_scope=WorkspaceScope.ROOT_ONLY,
        require_python_pin=False,
    )
    sources = PolicySources(
        require_remote=PolicySource.FLEET_POLICY,
        test_command=PolicySource.FLEET_POLICY,
        require_audit=PolicySource.FLEET_POLICY,
        fail_on_audit=PolicySource.FLEET_POLICY,
        audit_timeout_seconds=PolicySource.FLEET_POLICY,
        git_timeout_seconds=PolicySource.FLEET_POLICY,
        malware_check=PolicySource.FLEET_POLICY,
        workspace_mode=PolicySource.FLEET_POLICY,
        workspace_scope=PolicySource.DERIVED,
        require_python_pin=PolicySource.FLEET_POLICY,
    )
    return PolicySnapshot(
        captured_at=utc_now(),
        values=values,
        sources=sources,
    )
