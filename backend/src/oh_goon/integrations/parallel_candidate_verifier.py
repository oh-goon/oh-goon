"""Prepare the original verification Step's exact candidate and adjudicating inputs."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import MAX_REPOSITORY_BYTES, MAX_SOURCE_ENTRIES
from oh_goon.core.models.parallel_candidate_verification import (
    VERIFIER_INPUT_ROOT,
    VERIFIER_SOURCE_ROOT,
    ParallelCandidateVerifier,
    ParallelCandidateVerifierObservation,
)
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.content_snapshot_proof import snapshot_metadata_fingerprint
from oh_goon.integrations.fleet_plan_process_isolation import StandardReadOnlyMount
from oh_goon.integrations.source_snapshot import snapshot_source_directory, source_snapshot_identity


def require_verifier_executable(verifier: ParallelCandidateVerifier) -> None:
    """Verify the resolved executable bytes without rediscovering a native tool."""
    path = Path(verifier.executable)
    try:
        if path.resolve(strict=True) != path:
            raise OSError("Verifier executable is not canonical")
        digest = native_executable_sha256(path)
    except OSError as error:
        raise FleetPlanInvalid("Admitted verifier executable is unavailable") from error
    if digest != verifier.executable_sha256:
        raise FleetPlanInvalid("Admitted verifier executable changed")


def prepare_parallel_verifier(
    repository: Path,
    scratch: Path,
    verifier: ParallelCandidateVerifier,
    *,
    cancel_requested: Callable[[], bool] | None,
) -> tuple[SnapshotIdentity, str, tuple[StandardReadOnlyMount, ...]]:
    """Materialize candidate and trusted verifier inputs for this Step only."""
    require_verifier_executable(verifier)
    candidate = scratch / "parallel-candidate"
    input_dir = scratch / "parallel-inputs"
    workspace_fingerprint = snapshot_metadata_fingerprint(repository)
    source = snapshot_source_directory(
        repository,
        candidate,
        label="Parallel verifier candidate",
        max_bytes=MAX_REPOSITORY_BYTES,
        max_entries=MAX_SOURCE_ENTRIES,
        reject_dotenv_state=True,
        cancel_requested=cancel_requested,
    )
    input_dir.mkdir(mode=0o700)
    for item in verifier.inputs:
        destination = input_dir / item.name
        with destination.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(item.content.encode("utf-8"))
    if snapshot_metadata_fingerprint(repository) != workspace_fingerprint:
        raise FleetPlanInvalid("Parallel candidate changed before verification")
    return (
        source,
        workspace_fingerprint,
        (
            StandardReadOnlyMount(candidate, VERIFIER_SOURCE_ROOT),
            StandardReadOnlyMount(input_dir, VERIFIER_INPUT_ROOT),
        ),
    )


def observe_parallel_verifier_result(
    repository: Path,
    source: SnapshotIdentity,
    workspace_fingerprint: str,
    verifier: ParallelCandidateVerifier,
    *,
    cancel_requested: Callable[[], bool] | None,
) -> ParallelCandidateVerifierObservation:
    """Bind a native attempt to the snapshot it consumed and the current candidate."""
    try:
        require_verifier_executable(verifier)
        unchanged = (
            source
            == source_snapshot_identity(
                repository, cancel_requested=cancel_requested, reject_dotenv_state=True
            )
            and snapshot_metadata_fingerprint(repository) == workspace_fingerprint
        )
    except (OSError, FleetPlanInvalid):
        unchanged = False
    return ParallelCandidateVerifierObservation(
        verifier_id=verifier.identity,
        source_sha256=source.sha256,
        byte_count=source.byte_count,
        entry_count=source.entry_count,
        workspace_fingerprint=workspace_fingerprint,
        postcondition="UNCHANGED" if unchanged else "MISMATCH",
    )
