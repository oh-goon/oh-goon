from __future__ import annotations

import os
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.operation_economics import OperationEconomicsReceipt, economics_receipt
from oh_goon.core.path_authority import (
    RepositoryScopedPath,
    ensure_private_directory,
)
from oh_goon.core.path_layouts import ContentStorePathLayout
from oh_goon.integrations.exact_file_write import (
    ExactPublicationDurabilityError,
    publish_prepared_directory,
    replace_prepared_file,
    sync_prepared_path,
)
from oh_goon.integrations.fleet_content_object_lifecycle import (
    record_object_ready_locked,
    touch_object_used_locked,
)
from oh_goon.integrations.fleet_content_store_lifecycle import (
    claim_stage_owner,
    create_reference_locked,
    garbage_collect,
    new_lease_locked,
    object_id_for_reference,
    prune_orphan_staging,
    release_reference,
    remove_owned_stage,
    store_lock,
    validate_repository_id,
)

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_content_store_lifecycle import FleetContentLease


from oh_goon.integrations.content_snapshot import (
    SnapshotIdentity,
    copy_frozen_directory,
    snapshot_directory,
    snapshot_file,
    snapshot_identity,
    verify_snapshot,
)
from oh_goon.integrations.content_snapshot_proof import StrongTreeProof

ContentObjectKind = Literal["tree", "file"]


@dataclass(frozen=True, slots=True)
class FleetContentObject:
    """One immutable object retained beyond a single Fleet plan run."""

    object_id: str
    kind: ContentObjectKind
    path: Path
    identity: SnapshotIdentity
    reference_id: str
    owner_repository_id: str


def _emit_verified_tree_economics(
    economics: Callable[[OperationEconomicsReceipt], None] | None,
    *,
    expected_identity: SnapshotIdentity,
    reused: bool,
    source_proof_reused: bool = False,
    started: float,
) -> None:
    if economics is None:
        return
    if reused:
        receipt = economics_receipt(
            operation="retained-supply.bind",
            authority_owner="Oh-Goon retained content store",
            artifact_identity="sha256:" + expected_identity.sha256,
            outcome="REUSED",
            reason=(
                "existing immutable content object rebound using upstream source proof and retained-object re-proof"
                if source_proof_reused
                else "existing immutable content object rebound after source and retained-object re-proof"
            ),
            bytes_hashed=expected_identity.byte_count * (1 if source_proof_reused else 2),
            bytes_read=expected_identity.byte_count * (1 if source_proof_reused else 2),
            reused_bytes=expected_identity.byte_count,
            strong_proof_performed=True,
            strong_proof_reused=source_proof_reused,
            elapsed_seconds=time.perf_counter() - started,
        )
    else:
        receipt = economics_receipt(
            operation="retained-supply.bind",
            authority_owner="Oh-Goon retained content store",
            artifact_identity="sha256:" + expected_identity.sha256,
            outcome="PERFORMED",
            reason="new immutable content object copied and strongly identified before publication",
            bytes_hashed=expected_identity.byte_count,
            bytes_read=expected_identity.byte_count * 2,
            bytes_written=expected_identity.byte_count,
            bytes_copied=expected_identity.byte_count,
            strong_proof_performed=True,
            elapsed_seconds=time.perf_counter() - started,
        )
    economics(receipt)


class FleetContentStore:
    """Cross-run immutable store with durable references and race-safe read leases."""

    def __init__(self, root: Path) -> None:
        self.root = ensure_private_directory(root)
        self._layout = ContentStorePathLayout.from_root(self.root)
        for path in (
            self._layout.objects("tree"),
            self._layout.objects("file"),
            self._layout.staging,
            self._layout.references,
            self._layout.leases,
            self._layout.object_lifecycle / "tree",
            self._layout.object_lifecycle / "file",
        ):
            ensure_private_directory(path)
        self._lock_path = self._layout.lock

    def put_tree(
        self,
        source: RepositoryScopedPath,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> FleetContentObject:
        _require_repository_scoped_path(source, label="Fleet retained tree source")
        source.prove_owned()
        stage = self._stage_path("tree")
        try:
            identity = snapshot_directory(
                source.path,
                stage,
                label="Fleet content-store tree",
                cancel_requested=cancel_requested,
            )
            source.prove_owned()
            return self._commit_retained_stage(
                "tree",
                stage,
                identity,
                owner_repository_id=source.owner_repository_id,
            )
        finally:
            remove_owned_stage(stage)

    def put_verified_tree(
        self,
        source: RepositoryScopedPath,
        source_proof: StrongTreeProof,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        economics: Callable[[OperationEconomicsReceipt], None] | None = None,
    ) -> FleetContentObject:
        """Retain exact bytes that were already identified by the source owner.

        If the content-addressed object already exists, re-prove both the immutable
        source identity and the retained object and create only a new repository-scoped
        reference. This avoids copying a multi-gigabyte verified certification supply
        into a throwaway stage on every run while preserving the same identity checks.
        A missing object still follows the ordinary copy-and-verify publication path.
        """

        started = time.perf_counter()
        _require_repository_scoped_path(source, label="Fleet verified retained tree source")
        source.prove_owned()
        if source.path.resolve() != source_proof.source_path or not source_proof.proves_current(
            source.path
        ):
            raise FleetPlanInvalid(
                "Fleet verified retained tree strong proof is stale or belongs to another source"
            )
        expected_identity = source_proof.identity
        source_root_mode = stat.S_IMODE(source.path.stat(follow_symlinks=False).st_mode)
        reused = self._reuse_verified_tree(
            source,
            source_proof,
            source_root_mode=source_root_mode,
            cancel_requested=cancel_requested,
        )
        if reused is not None:
            retained = reused
            _emit_verified_tree_economics(
                economics,
                expected_identity=expected_identity,
                reused=True,
                source_proof_reused=True,
                started=started,
            )
            return retained

        stage = self._stage_path("tree")
        try:
            observed = copy_frozen_directory(
                source.path,
                stage,
                label="Fleet verified content-store tree",
                cancel_requested=cancel_requested,
            )
            source.prove_owned()
            if observed != expected_identity:
                raise FleetPlanInvalid("Fleet verified retained tree identity mismatch")
            retained = self._commit_retained_stage(
                "tree",
                stage,
                observed,
                owner_repository_id=source.owner_repository_id,
            )
            _emit_verified_tree_economics(
                economics, expected_identity=expected_identity, reused=False, started=started
            )
            return retained
        finally:
            remove_owned_stage(stage)

    def _reuse_verified_tree(
        self,
        source: RepositoryScopedPath,
        source_proof: StrongTreeProof,
        *,
        source_root_mode: int,
        cancel_requested: Callable[[], bool] | None,
    ) -> FleetContentObject | None:
        expected_identity = source_proof.identity
        object_id = _object_id("tree", expected_identity.sha256)
        destination = self._object_path(object_id)
        if not destination.exists():
            return None

        # The source was already strongly proven by its authority owner. Re-check only
        # the mutation guard here; do not rediscover the source identity.
        source.prove_owned()
        if not source_proof.proves_current(source.path):
            raise FleetPlanInvalid("Fleet verified retained tree strong proof became stale")
        source.prove_owned()

        with store_lock(self._lock_path):
            if not destination.exists():
                return None
            if destination.is_symlink() or not destination.is_dir():
                raise FleetPlanInvalid(f"Existing Fleet content object is unsafe: {object_id}")

            verify_snapshot(
                destination,
                expected_identity,
                label=f"existing {object_id}",
                cancel_requested=cancel_requested,
            )
            retained_mode = stat.S_IMODE(destination.stat(follow_symlinks=False).st_mode)
            if retained_mode != source_root_mode:
                raise FleetPlanInvalid(
                    f"existing {object_id} root mode differs from prepared immutable tree"
                )
            touch_object_used_locked(self.root, object_id, byte_count=expected_identity.byte_count)
            reference_id = create_reference_locked(
                self.root, object_id, owner_repository_id=source.owner_repository_id
            )
            return FleetContentObject(
                object_id,
                "tree",
                destination,
                expected_identity,
                reference_id,
                source.owner_repository_id,
            )

    def put_frozen_bytes(self, data: bytes, *, owner_repository_id: str) -> FleetContentObject:
        """Retain already-verified AG-02 bytes in this canonical content store.

        The typed resolved Plan and journal carry only a digest and file reference.
        Never create an Agent-specific data store or re-open the original Skill.
        """
        if type(data) is not bytes or len(data) > 120 * 1024 * 1024:
            raise FleetPlanInvalid("frozen Agent packet is not bounded immutable bytes")
        validate_repository_id(owner_repository_id)
        stage = self._stage_path("file")
        try:
            with stage.open("xb") as sink:
                sink.write(data)
                sink.flush()
                os.fsync(sink.fileno())
            identity = snapshot_identity(stage)
            return self._commit_retained_stage(
                "file",
                stage,
                identity,
                owner_repository_id=owner_repository_id,
            )
        finally:
            remove_owned_stage(stage)

    def put_file(
        self,
        source: RepositoryScopedPath,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> FleetContentObject:
        _require_repository_scoped_path(source, label="Fleet retained file source")
        source.prove_owned()
        stage = self._stage_path("file")
        ensure_private_directory(stage.parent)
        try:
            identity = snapshot_file(
                source.path,
                stage,
                label="Fleet content-store file",
                cancel_requested=cancel_requested,
            )
            source.prove_owned()
            return self._commit_retained_stage(
                "file",
                stage,
                identity,
                owner_repository_id=source.owner_repository_id,
            )
        finally:
            remove_owned_stage(stage)

    def materialize_tree(
        self,
        reference_id: str,
        destination: RepositoryScopedPath,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SnapshotIdentity:
        _require_repository_scoped_path(destination, label="Fleet retained tree destination")
        destination.prove_scope()
        owner_repository_id = destination.owner_repository_id
        with self.acquire_lease(
            reference_id, owner_repository_id=owner_repository_id, expected_kind="tree"
        ) as lease:
            _kind, expected_sha256 = _parse_object_id(lease.object_id)
            object_path = self._object_path(lease.object_id)
            if not object_path.is_dir():
                raise FleetPlanInvalid(
                    f"Fleet content object for reference is unavailable: {reference_id}"
                )
            observed = copy_frozen_directory(
                object_path,
                destination.path,
                label=f"Fleet content reference {reference_id}",
                cancel_requested=cancel_requested,
            )
            destination.prove_owned()
            if observed.sha256 != expected_sha256:
                raise FleetPlanInvalid(
                    f"Fleet content object changed while materializing reference: {reference_id}"
                )
            with store_lock(self._lock_path):
                touch_object_used_locked(self.root, lease.object_id, byte_count=observed.byte_count)
            return observed

    def get(
        self,
        reference_id: str,
        *,
        owner_repository_id: str,
        expected_kind: ContentObjectKind | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> FleetContentObject:
        with store_lock(self._lock_path):
            object_id = object_id_for_reference(
                self._layout,
                reference_id=reference_id,
                owner_repository_id=owner_repository_id,
            )
            kind, sha256 = _parse_object_id(object_id)
            if expected_kind is not None and kind != expected_kind:
                raise FleetPlanInvalid(
                    f"Fleet content reference {reference_id} has kind {kind}, expected {expected_kind}"
                )
            path = self._object_path(object_id)
            if not path.exists():
                raise FleetPlanInvalid(
                    f"Fleet content object for reference is unavailable: {reference_id}"
                )
            identity = snapshot_identity(path, cancel_requested=cancel_requested)
            if identity.sha256 != sha256:
                raise FleetPlanInvalid(f"Fleet content object identity mismatch: {object_id}")
            touch_object_used_locked(self.root, object_id, byte_count=identity.byte_count)
            return FleetContentObject(
                object_id, kind, path, identity, reference_id, owner_repository_id
            )

    def preflight_reference_metadata(
        self,
        reference_id: str,
        *,
        owner_repository_id: str,
        expected_kind: ContentObjectKind | None = None,
    ) -> None:
        """Reject deterministic retained-reference defects without hashing object bytes.

        This is intentionally cheap admission evidence only. Authoritative preparation
        still acquires a lease and verifies/materializes the immutable object identity.
        """

        with store_lock(self._lock_path):
            object_id = object_id_for_reference(
                self._layout,
                reference_id=reference_id,
                owner_repository_id=owner_repository_id,
            )
            kind, _sha256 = _parse_object_id(object_id)
            if expected_kind is not None and kind != expected_kind:
                raise FleetPlanInvalid(
                    f"Fleet content reference {reference_id} has kind {kind}, expected {expected_kind}"
                )
            path = self._object_path(object_id)
            kind_matches = path.is_dir() if kind == "tree" else path.is_file()
            if not kind_matches:
                raise FleetPlanInvalid(
                    f"Fleet content object for reference is unavailable: {reference_id}"
                )

    def acquire_lease(
        self,
        reference_id: str,
        *,
        owner_repository_id: str,
        expected_kind: ContentObjectKind | None = None,
    ) -> FleetContentLease:
        with store_lock(self._lock_path):
            object_id = object_id_for_reference(
                self._layout,
                reference_id=reference_id,
                owner_repository_id=owner_repository_id,
            )
            kind, _ = _parse_object_id(object_id)
            if expected_kind is not None and kind != expected_kind:
                raise FleetPlanInvalid(
                    f"Fleet content reference {reference_id} has kind {kind}, expected {expected_kind}"
                )
            if not self._object_path(object_id).exists():
                raise FleetPlanInvalid(
                    f"Fleet content object for reference is unavailable: {reference_id}"
                )
            return new_lease_locked(
                self._layout,
                self._lock_path,
                object_id=object_id,
                owner_repository_id=owner_repository_id,
            )

    def release_reference(self, reference_id: str, *, owner_repository_id: str) -> None:
        release_reference(
            self._layout,
            self._lock_path,
            reference_id=reference_id,
            owner_repository_id=owner_repository_id,
        )

    def garbage_collect(self) -> tuple[str, ...]:
        """Delete only immutable objects with neither a durable reference nor a live read lease."""

        return garbage_collect(self.root, self._layout, self._lock_path)

    def prune_orphan_staging(self):
        """Reclaim only staging with positive dead-creator birth evidence."""

        return prune_orphan_staging(self._layout, self._lock_path)

    def prune_to_retention_budget(
        self,
        budget,
        *,
        now_ns: int | None = None,
        limits=None,
    ):
        """Retain reusable unreachable content until explicit byte/count pressure requires eviction."""

        from oh_goon.integrations.fleet_content_retention import prune_to_retention_budget

        return prune_to_retention_budget(
            self.root,
            self._layout,
            self._lock_path,
            budget,
            now_ns=now_ns,
            limits=limits,
        )

    def reconcile_interrupted_maintenance(self):
        from oh_goon.integrations.fleet_content_crash_reconciliation import (
            reconcile_interrupted_maintenance,
        )

        return reconcile_interrupted_maintenance(self.root, self._layout, self._lock_path)

    def maintain_retained_content(self, policy=None, *, now_ns: int | None = None):
        from oh_goon.integrations.fleet_content_maintenance import run_content_maintenance

        return run_content_maintenance(self, policy, now_ns=now_ns)

    def _commit_retained_stage(
        self,
        kind: ContentObjectKind,
        stage: Path,
        identity: SnapshotIdentity,
        *,
        owner_repository_id: str,
    ) -> FleetContentObject:
        object_id = _object_id(kind, identity.sha256)
        destination = self._object_path(object_id)
        with store_lock(self._lock_path):
            validate_repository_id(owner_repository_id)
            _commit_stage(stage, destination, identity, label=object_id)
            record_object_ready_locked(self.root, object_id, byte_count=identity.byte_count)
            reference_id = create_reference_locked(
                self.root, object_id, owner_repository_id=owner_repository_id
            )
            return FleetContentObject(
                object_id, kind, destination, identity, reference_id, owner_repository_id
            )

    def _object_path(self, object_id: str) -> Path:
        kind, sha256 = _parse_object_id(object_id)
        return self._layout.object_path(kind, sha256)

    def _stage_path(self, kind: ContentObjectKind) -> Path:
        stage = self._layout.stage(kind, uuid.uuid4().hex)
        claim_stage_owner(stage)
        return stage


def _commit_stage(
    stage: Path, destination: Path, identity: SnapshotIdentity, *, label: str
) -> None:
    if destination.exists():
        _verify_existing_stage(stage, destination, identity, label=f"existing {label}")
        return
    ensure_private_directory(destination.parent)
    sync_prepared_path(stage)
    try:
        if stage.is_dir():
            publish_prepared_directory(stage, destination)
        else:
            replace_prepared_file(stage, destination)
    except ExactPublicationDurabilityError:
        raise
    except FileExistsError:
        _verify_existing_stage(stage, destination, identity, label=f"concurrent {label}")
    except OSError:
        if destination.exists():
            _verify_existing_stage(stage, destination, identity, label=f"concurrent {label}")
            return
        raise


def _verify_existing_stage(
    stage: Path, destination: Path, identity: SnapshotIdentity, *, label: str
) -> None:
    verify_snapshot(destination, identity, label=label)
    if stage.is_dir() and destination.is_dir():
        prepared_mode = stat.S_IMODE(stage.stat(follow_symlinks=False).st_mode)
        retained_mode = stat.S_IMODE(destination.stat(follow_symlinks=False).st_mode)
        if retained_mode != prepared_mode:
            raise FleetPlanInvalid(f"{label} root mode differs from prepared immutable tree")


def _object_id(kind: ContentObjectKind, sha256: str) -> str:
    return f"{kind}-sha256-{sha256}"


def _parse_object_id(object_id: str) -> tuple[ContentObjectKind, str]:
    parts = object_id.split("-sha256-", 1)
    if len(parts) != 2 or parts[0] not in {"tree", "file"}:
        raise FleetPlanInvalid(f"Invalid Fleet content object id: {object_id}")
    kind = parts[0]
    sha256 = parts[1]
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise FleetPlanInvalid(f"Invalid Fleet content object digest: {object_id}")
    return kind, sha256  # type: ignore[return-value]


def _require_repository_scoped_path(value: object, *, label: str) -> None:
    if not isinstance(value, RepositoryScopedPath):
        raise FleetPlanInvalid(
            f"{label} requires RepositoryScopedPath authority; raw host paths are forbidden"
        )
