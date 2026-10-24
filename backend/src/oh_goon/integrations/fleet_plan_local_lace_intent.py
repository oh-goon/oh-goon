"""B12C: explicit original-journal prework bridge for a local physical attempt.

Development interface only: the original Fleet admission owner must construct the
reservation; neither Bubblewrap nor the plan may mint signing keys or fallback
on missing authority. Physical containment and teardown are separate gates.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_layouts import SANDBOX_PATHS
from oh_goon.core.ports.original_mount_intent import (
    OriginalHostToolMount,
    OriginalMountIntentDescriptor,
    OriginalNativeMount,
    OriginalSandboxMount,
)

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest


def _identity(label: str, data: dict[str, object]) -> str:
    return digest("oh-goon/b12c/" + label + "/v1", canonical_bytes(data))


AGENT_SOURCE_SANDBOX_TARGET = PurePosixPath("/opt/oh-goon/agent-source")


def require_original_agent_source_mount_authority(request: IsolatedProcessRequest) -> None:
    """Fail before effects if unadmitted staged Agent bytes enter native mounts.

    AG-02B2B3B3A: original signed stage + read-only descriptor are PREWORK,
    not a granted RESOURCE_INTENT or an actual qualified physical mount.
    This guard belongs to the existing LACE request projection. The same
    function is enforced by the Bubblewrap entrypoint before allocation.
    Remove the denial only when original signed mount-intent correspondence,
    exact consumption and physical observer custody are implemented together.
    """
    attempt = request.attempt
    # A signed stage proposal is not itself a native effect permission. An
    # explicit source descriptor cannot bypass the original physical refusal.
    if getattr(request, "agent_source_mount", None) is not None:
        raise FleetPlanInvalid("Agent source native mount lacks qualified LACE effect custody")
    stage = attempt.paths.root / "agent-source-prework"
    stage_absolute = Path(os.path.abspath(stage))
    stage_resolved = Path(os.path.realpath(stage))

    def deny_stage_path(value: str | os.PathLike[str] | None, *, label: str) -> None:
        if value is None:
            return
        path = Path(os.path.abspath(value))
        resolved = Path(os.path.realpath(value))
        if any(
            p == protected or protected in p.parents
            for p in (path, resolved)
            for protected in (stage_absolute, stage_resolved)
        ):
            raise FleetPlanInvalid(
                f"{label} cannot expose staged Agent source without original LACE custody"
            )

    for binding in attempt.sandbox_bindings:
        target = getattr(binding, "sandbox_path", None)
        if target is not None:
            # Guard both canonical and noncanonical spellings; later path
            # validation remains authoritative for rejecting bad syntax.
            raw = str(target)
            target_path = PurePosixPath(raw)
            if (
                target_path == AGENT_SOURCE_SANDBOX_TARGET
                or AGENT_SOURCE_SANDBOX_TARGET in target_path.parents
                or raw.startswith(str(AGENT_SOURCE_SANDBOX_TARGET) + "/")
            ):
                raise FleetPlanInvalid(
                    "Agent source mount requires original authenticated LACE custody"
                )
        deny_stage_path(getattr(binding, "snapshot_path", None), label="Managed input")
    for tool in attempt.host_tools:
        deny_stage_path(getattr(tool, "snapshot_path", None), label="Host tool")
        deny_stage_path(getattr(tool, "execution_snapshot_path", None), label="Host tool")
    workspace = getattr(request, "read_only_workspace", None)
    if workspace is not None:
        deny_stage_path(workspace.snapshot_path, label="Read-only workspace")


def prepared_original_native_mounts(
    request: IsolatedProcessRequest,
) -> tuple[OriginalNativeMount, ...]:
    """One pre-effect native bind roster from the ORIGINAL prepared Step attempt.

    The runtime root, writable paths and generated etc files can be signed
    before any physical allocation. The dynamically allocated nested cgroup
    source and memfd objects must be separately pinned after allocation and
    cannot be claimed by this pre-effect roster.
    """
    attempt = request.attempt
    paths = attempt.paths
    mounts = [
        OriginalNativeMount(
            str(attempt.runtime_root.root),
            "/",
            "--ro-bind",
        )
    ]
    mounts.extend(
        OriginalNativeMount(
            str(paths.etc / name),
            str(SANDBOX_PATHS.etc / name),
            "--ro-bind",
        )
        for name in ("passwd", "group", "nsswitch.conf", "hosts")
    )
    if request.read_only_workspace is None:
        mounts.append(
            OriginalNativeMount(
                str(paths.workspace),
                str(SANDBOX_PATHS.workspace),
                "--bind",
            )
        )
    else:
        # Read-only workspace has one existing signed source identity; include
        # its real Bubblewrap mode in this complete static native roster too.
        mounts.append(
            OriginalNativeMount(
                str(request.read_only_workspace.snapshot_path),
                str(SANDBOX_PATHS.workspace),
                "--ro-bind",
            )
        )
    mounts.extend(
        OriginalNativeMount(str(getattr(paths, name)), str(getattr(SANDBOX_PATHS, name)), "--bind")
        for name in ("home", "tmp")
    )
    if not any(binding.sandbox_path == SANDBOX_PATHS.cache for binding in attempt.sandbox_bindings):
        mounts.append(OriginalNativeMount(str(paths.cache), str(SANDBOX_PATHS.cache), "--bind"))
    mounts.append(OriginalNativeMount(str(paths.config), str(SANDBOX_PATHS.config), "--bind"))
    # Use the SAME immutable signed stage projection as the future bwrap FD
    # source lease, never a separately constructed Agent mount roster.
    if getattr(request, "agent_source_mount", None) is not None:
        from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount

        source = getattr(request, "agent_source_mount", None)
        if (
            type(source) is not OriginalSandboxMount
            or source.target != str(AGENT_SOURCE_SANDBOX_TARGET)
            or source.source != str(paths.root / "agent-source-prework")
        ):
            raise FleetPlanInvalid("Agent native source differs from original staged attempt")
        mounts.append(OriginalNativeMount(source.source, source.target, "--ro-bind"))
    return tuple(mounts)


def local_resource_identities(request: IsolatedProcessRequest) -> dict[str, str]:
    """Derive resource intent from the *actual* prepared attempt, not caller labels.

    This pre-effect projection does not claim the native objects have been created.
    """
    require_original_agent_source_mount_authority(request)
    attempt = request.attempt
    paths = attempt.paths
    return {
        "workspace": _identity(
            "workspace",
            {
                "attempt_root": str(paths.root),
                "workspace": str(paths.workspace),
                "cache": str(paths.cache),
                "cwd": str(request.cwd),
            },
        ),
        "runtime": _identity(
            "runtime",
            {
                "root": str(attempt.runtime_root.root),
                "identity": str(attempt.runtime_root.identity.sha256),
                "uv_python": str(attempt.runtime_root.uv_system_python),
            },
        ),
        "network": _identity("network", {"policy": attempt.egress.model_dump(mode="json")}),
        "cgroup": _identity(
            "cgroup",
            {
                "policy": attempt.resources.model_dump(mode="json"),
                "nested": attempt.nested_strict_execution,
            },
        ),
        # B3B3B1: the B12C descriptor is the SINGLE mount identity owner.
        # A second ad-hoc serialization here would permit semantic drift
        # between signed RESOURCE_INTENT and physical recovery scope.
        "mounts": OriginalMountIntentDescriptor(
            tuple(
                OriginalSandboxMount(
                    source=str(b.snapshot_path),
                    target=str(b.sandbox_path),
                    sha256=b.identity.sha256,
                )
                for b in attempt.sandbox_bindings
            ),
            tuple(
                OriginalHostToolMount(
                    name=t.name,
                    source=str(t.execution_snapshot_path),
                    sha256=t.execution_identity.sha256,
                )
                for t in attempt.host_tools
            ),
            read_only_workspace=(
                OriginalSandboxMount(
                    source=str(request.read_only_workspace.snapshot_path),
                    target=str(SANDBOX_PATHS.workspace),
                    sha256=request.read_only_workspace.identity.sha256,
                )
                if request.read_only_workspace is not None
                else None
            ),
            native_mounts=prepared_original_native_mounts(request),
            agent_source_mount=getattr(request, "agent_source_mount", None),
        ).resource_identity,
    }
