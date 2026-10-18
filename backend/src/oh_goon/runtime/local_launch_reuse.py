from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from oh_goon.core.launch_manifest import LaunchIntent, LaunchManifest
from oh_goon.core.launch_status import LaunchOwnerProof, SafeLaunchStatus, safe_launch_status
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.runtime.local_launch_binding import (
    LocalLaunchBinding,
    LocalLaunchBindingStore,
    require_binding_matches_manifest,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


class LocalLaunchReuseAction(StrEnum):
    START_NEW = "start_new"
    REUSE = "reuse"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class LocalLaunchReuseDecision:
    """Identity-bound decision for one exact workspace launch request.

    This is a read-only admission decision. It never starts, stops, kills, repairs,
    replaces, or rebinds a launch. The existing launch owner remains authoritative.
    """

    action: LocalLaunchReuseAction
    code: str
    reason: str
    binding: LocalLaunchBinding | None = None
    manifest: LaunchManifest | None = None
    status: SafeLaunchStatus | None = None

    @property
    def launch_id(self) -> str | None:
        return None if self.binding is None else self.binding.launch_id


def assess_local_launch_reuse(
    *,
    workspace: Path | str,
    intent: LaunchIntent,
    binding_store: LocalLaunchBindingStore,
    event_store: LocalLaunchEventStore,
    process_observer: Callable[[int], ProcessBirthIdentity | None] = observe_process_birth,
) -> LocalLaunchReuseDecision:
    """Decide whether one requested local launch may start, reuse, or must conflict.

    Compatibility requires the exact workspace binding, exact resolved launch-intent
    identity, matching owner generation, and positive live owner birth proof. A
    nonterminal launch whose owner cannot be proven is never reused or replaced.
    """

    try:
        binding = binding_store.resolve_optional(workspace)
    except (OSError, ValueError) as exc:
        return LocalLaunchReuseDecision(
            action=LocalLaunchReuseAction.CONFLICT,
            code="binding_unavailable",
            reason=f"current workspace launch binding is unavailable: {exc}",
        )
    if binding is None:
        return LocalLaunchReuseDecision(
            action=LocalLaunchReuseAction.START_NEW,
            code="no_binding",
            reason="workspace has no current local launch binding",
        )

    try:
        manifest = event_store.manifest(binding.launch_id)
        require_binding_matches_manifest(binding, manifest)
    except (OSError, ValueError) as exc:
        return LocalLaunchReuseDecision(
            action=LocalLaunchReuseAction.CONFLICT,
            code="bound_launch_unavailable",
            reason=f"bound launch cannot be safely resolved: {exc}",
            binding=binding,
        )

    if manifest.lifecycle.is_terminal:
        return LocalLaunchReuseDecision(
            action=LocalLaunchReuseAction.START_NEW,
            code="previous_terminal",
            reason=f"previous bound launch is terminal ({manifest.lifecycle.state.value})",
            binding=binding,
            manifest=manifest,
        )

    status = safe_launch_status(manifest, process_observer=process_observer)
    requested_identity = intent.identity()
    if manifest.intent.profile != intent.profile or binding.intent_identity != requested_identity:
        return LocalLaunchReuseDecision(
            action=LocalLaunchReuseAction.CONFLICT,
            code="intent_mismatch",
            reason=(
                "current workspace launch has different resolved launch intent "
                f"({manifest.intent.profile}); refusing parallel ownership"
            ),
            binding=binding,
            manifest=manifest,
            status=status,
        )

    if status.owner_proof is not LaunchOwnerProof.AVAILABLE:
        return LocalLaunchReuseDecision(
            action=LocalLaunchReuseAction.CONFLICT,
            code="owner_unproven",
            reason=(
                "current workspace launch is nonterminal but the exact owner process "
                "birth identity cannot be proven; reuse and replacement are refused"
            ),
            binding=binding,
            manifest=manifest,
            status=status,
        )

    return LocalLaunchReuseDecision(
        action=LocalLaunchReuseAction.REUSE,
        code="compatible_live_owner",
        reason="exact workspace, resolved intent, owner generation, and live owner birth identity match",
        binding=binding,
        manifest=manifest,
        status=status,
    )

