"""Observe present source against a retained candidate without resuming its Run."""

from __future__ import annotations

import hashlib
import subprocess
from functools import partial
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid, ReviewedRepositorySourceStale
from oh_goon.core.models.agent_continuation import (
    AgentContinuationDiff,
    AgentContinuationSourceInspection,
)
from oh_goon.core.models.agent_source import MAX_PREWORK_PACKET_BYTES, FrozenAgentStepHandoff
from oh_goon.core.models.fleet_plan import ResolvedAgentStep
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.engine.command import run_command
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.hashmarks import prepare_agent_evidence
from oh_goon.integrations.source_snapshot import snapshot_source_directory
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.agent_authoring_sources import require_native_agent_source_path_privacy
from oh_goon.services.repositories import RepositoryService

_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_ENTRIES = 4096
_MAX_DIFF_BYTES = 65536


def _retained_candidate(
    previous: FleetRunDetail, content_store: FleetContentStore, repository_id: str
) -> tuple[FrozenAgentStepHandoff, str]:
    target = previous.run.target
    if (
        not isinstance(target, FleetPlanTarget)
        or len(previous.run.targets) != 1
        or previous.run.targets[0].repository_id != repository_id
        or not target.agent_deliveries
    ):
        raise FleetPlanInvalid("The original captured candidate is unavailable for this repository")
    reference = target.agent_deliveries[0]
    step = next((item for item in target.plan.steps if item.step_id == reference.step_id), None)
    if (
        not isinstance(step, ResolvedAgentStep)
        or step.handoff_identity != reference.handoff_identity
        or step.delivery_sha256 != reference.delivery_sha256
        or (
            step.step_id != step.source_step_id
            and not step.step_id.endswith("." + step.source_step_id)
        )
    ):
        raise FleetPlanInvalid(
            "The original captured candidate has a different resolved Step owner"
        )
    with content_store.acquire_lease(
        reference.reference_id, owner_repository_id=repository_id, expected_kind="file"
    ):
        stored = content_store.get(
            reference.reference_id, owner_repository_id=repository_id, expected_kind="file"
        )
        if stored.identity.byte_count > MAX_PREWORK_PACKET_BYTES:
            raise FleetPlanInvalid("The original captured candidate exceeds its delivery bound")
        with stored.path.open("rb") as stream:
            packet = stream.read(MAX_PREWORK_PACKET_BYTES + 1)
        if (
            len(packet) > MAX_PREWORK_PACKET_BYTES
            or hashlib.sha256(packet).hexdigest() != reference.delivery_sha256
        ):
            raise FleetPlanInvalid(
                "The original captured candidate delivery is unavailable or changed"
            )
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=reference.handoff_identity
    )
    if handoff.step_id != step.source_step_id or handoff.harness != step.workload.harness:
        raise FleetPlanInvalid("The original captured candidate belongs to a different Step")
    context = target.agent_repair
    if (
        context is not None
        and context.candidate_identity is not None
        and context.candidate_identity != handoff.source.repository.content_identity
    ):
        raise FleetPlanInvalid("The original captured candidate differs from its recorded campaign")
    return handoff, reference.delivery_sha256


def _native_diff(git: GitService, root: Path) -> AgentContinuationDiff:
    # This trusted observation compares only owned temporary captures. An Agent
    # runner is scoped to its registered workspace and cannot own this cwd.
    # Keep the selected native executable and original command/process owner.
    runner = partial(
        run_command,
        max_capture_bytes_per_stream=_MAX_DIFF_BYTES,
        retain_output_tail=True,
        preserve_stdout_whitespace=True,
    )
    try:
        result = git.run(
            "--no-pager",
            "diff",
            "--no-index",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--",
            "repo",
            "current",
            cwd=root,
            command_runner=runner,
        )
    except (OSError, subprocess.TimeoutExpired):
        return AgentContinuationDiff(
            availability="unavailable",
            reason="Native Git diff could not complete; no absence of changes is established.",
        )
    if result.returncode not in (0, 1):
        return AgentContinuationDiff(
            availability="unavailable",
            reason="Native Git refused the candidate comparison; no absence of changes is established.",
        )
    encoded = result.stdout.encode("utf-8")
    text = encoded[:_MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
    truncated = result.stdout_truncated or len(encoded) > _MAX_DIFF_BYTES
    return AgentContinuationDiff(
        availability="available",
        text=text,
        truncated=truncated,
        reason=(
            "Bounded native Git output retains only a diagnostic tail; the complete diff is omitted. "
            if truncated
            else "Native Git compares the captured candidate bytes with the current filtered source. "
        )
        + "Original capture did not retain executable modes; mode lines do not prove historical mode changes.",
        byte_count=result.stdout_evidence.byte_count
        if result.stdout_evidence is not None
        else None,
        # The command owner retains a diagnostic marker and decoded UTF-8 tail.
        # Its whole-stream raw count cannot prove the omitted raw byte count.
        omitted_bytes=None if truncated else 0,
    )


def inspect_continuation_source(
    *,
    repositories: RepositoryService,
    repository_id: str,
    previous: FleetRunDetail,
    content_store: FleetContentStore,
    git_service: GitService | None,
    expected_repository_instance_id: str | None = None,
) -> AgentContinuationSourceInspection:
    """Inspect one registered subject; old admission metadata stays historical.

    ``previous`` is the retained campaign Run containing the selected captured
    candidate. The optional expected token is a *current* subject observation,
    never that Run's ancient filesystem metadata or a Git-only identity.
    """
    repository = repositories.require_included(repository_id)
    with RepositoryWorkspaceAuthority.pin_repository_subject(repository.path) as require_current:
        subject = repository_subject_instance_id(repository.path)
        if (
            expected_repository_instance_id is not None
            and subject != expected_repository_instance_id
        ):
            raise ReviewedRepositorySourceStale("The inspected repository subject changed")
        handoff, delivery_sha256 = _retained_candidate(previous, content_store, repository_id)
        with prepare_agent_evidence(handoff) as prepared:
            current = snapshot_source_directory(
                repository.path,
                prepared.root / "current",
                label="continuation inspection source",
                max_bytes=_MAX_SOURCE_BYTES,
                max_entries=_MAX_SOURCE_ENTRIES,
                source_path_validator=require_native_agent_source_path_privacy,
            )
            diff = _native_diff(
                git_service if git_service is not None else GitService(), prepared.root
            )
            require_current()
            if (
                repositories.require_included(repository_id) != repository
                or repository_subject_instance_id(repository.path) != subject
            ):
                raise ReviewedRepositorySourceStale("The repository changed during inspection")
    return AgentContinuationSourceInspection(
        source_authority=RepositorySourceAuthority.create(
            subject_instance_id=subject,
            source_snapshot=PlanSnapshotEvidence(
                sha256=current.sha256,
                byte_count=current.byte_count,
                entry_count=current.entry_count,
            ),
        ),
        repository_instance_id=subject,
        diff=diff,
        recorded_candidate_identity=handoff.source.repository.content_identity,
        original_delivery_sha256=delivery_sha256,
    )
