from __future__ import annotations

import hashlib
import os
import posixpath
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import TYPE_CHECKING, cast

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedPlanStep
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepTeardownEvidence,
    FailedPlanStepTeardownEvidence,
    PlanExecutionInputEvidence,
    PlanIsolationBackendEvidence,
    PlanStepTeardownEvidence,
)
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.fleet_plan_step_contract import PlanStepEgress
from oh_goon.core.models.fleet_plan_step_primitives import PlanStreamOutput
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent, TrustedLocalRunScope
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt
from oh_goon.core.path_authority import (
    AttemptWorkspaceAuthority,
    RepositoryPathSpace,
    RepositoryScopedPath,
    StrictHostPathAuthority,
    canonical_path,
    ensure_private_directory,
)
from oh_goon.core.path_layouts import (
    SANDBOX_PATHS,
    AttemptPathLayout,
    RunPathLayout,
)
from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount
from oh_goon.core.source_tree_policy import EXECUTION_RUNTIME_MATERIALIZATION_ROOTS
from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
from oh_goon.integrations._fleet_plan_run_lineage import _RunLineage
from oh_goon.integrations._fleet_plan_run_resources import _FleetPlanRunInputs
from oh_goon.integrations._fleet_plan_runtime_root import RuntimeRootBinding
from oh_goon.integrations.content_snapshot import (
    SnapshotBinding,
    SnapshotIdentity,
    copy_frozen_directory,
    snapshot_file,
    snapshot_identity,
)
from oh_goon.integrations.content_snapshot_dereferenced import snapshot_directory_dereferenced
from oh_goon.integrations.executable_materialization_authority import (
    StepExecutableMaterializationReceipt,
    bind_step_executable_materialization,
)
from oh_goon.integrations.fleet_content_store import FleetContentObject, FleetContentStore
from oh_goon.integrations.fleet_plan_managed_runtime import (
    ManagedRuntimeSpec,
    UvProjectManagedRuntime,
)

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_plan_process_isolation import ProcessIsolationBackend


@dataclass(frozen=True, slots=True)
class SealedAttemptOutputs:
    """Capability minted only after one physical attempt passes ownership sealing."""

    attempt_root: Path
    workspace: Path
    cache: Path
    _authority: object = field(repr=False, compare=False)

    def prove_for(self, attempt: "FleetPlanStepAttempt") -> None:
        if (
            self._authority is not attempt._output_publication_authority
            or self.attempt_root != canonical_path(attempt.paths.root)
            or self.workspace != canonical_path(attempt.paths.workspace)
            or self.cache != canonical_path(attempt.paths.cache)
        ):
            raise FleetPlanInvalid(
                "Sealed attempt-output authority does not match physical attempt"
            )


@dataclass(frozen=True, slots=True)
class FleetPlanStepAttempt:
    """One disposable physical workspace plus private process directories."""

    paths: AttemptPathLayout
    cache_identity: SnapshotIdentity
    egress: PlanStepEgress
    host_tools: tuple[HostToolBinding, ...]
    sandbox_bindings: tuple[SnapshotBinding, ...]
    managed_runtime: ManagedRuntimeSpec | None
    host_environment: Mapping[str, str]
    runtime_root: RuntimeRootBinding
    isolation_backend: PlanIsolationBackendEvidence
    resources: CicdResourcePolicy
    nested_strict_execution: bool
    executable_materialization: StepExecutableMaterializationReceipt
    attempt_number: int
    _output_publication_authority: object = field(default_factory=object, repr=False, compare=False)

    def working_directory(self, step: ResolvedPlanStep) -> Path:
        value = getattr(step, "working_directory", ".")
        return RepositoryPathSpace.from_root(self.paths.workspace).resolve(
            value,
            label=f"Fleet plan step {step.step_id} working_directory",
            kind="directory",
            require_exists=True,
        )

    def seal_owned_outputs(self) -> SealedAttemptOutputs:
        """Materialize sandbox-runtime links and mint the only strict publication capability."""

        _materialize_generated_runtime_links(self)
        RepositoryPathSpace.from_root(self.paths.workspace).require_owned_tree()
        RepositoryPathSpace.from_root(self.paths.cache).require_owned_tree()
        return SealedAttemptOutputs(
            attempt_root=canonical_path(self.paths.root),
            workspace=canonical_path(self.paths.workspace),
            cache=canonical_path(self.paths.cache),
            _authority=self._output_publication_authority,
        )

    def _stage_agent_prework_bytes(
        self,
        handoff: object,
        *,
        require_kernel_file_verity: bool = False,
        require_kernel_directory_immutable: bool = False,
    ) -> dict[str, object]:
        """AG-02B2B3: one original attempt-owned, host-private staging operation.

        Only the FleetRunLifecycle admission/attempt fence may call this. The
        original packet decoder has already validated all source semantics.
        This is NOT a sandbox bind, an executable workload or Gate B evidence.
        An incomplete directory after a crash remains unusable and denies replay.
        """
        from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest

        if require_kernel_directory_immutable and not require_kernel_file_verity:
            raise FleetPlanInvalid("Kernel directory custody also requires file fs-verity")

        source = getattr(handoff, "source", None)
        if not callable(getattr(handoff, "verify", None)) or source is None:
            raise FleetPlanInvalid("original Agent Step frozen source required")
        handoff = cast(FrozenAgentStepHandoff, handoff)
        handoff.verify()
        trees = (source.repository, source.skill)
        for tree in trees:
            tree.verify()
        if self.attempt_number < 1:
            raise FleetPlanInvalid("original Agent Step attempt number required")
        # The established attempt root, never a caller-provided staging path.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            root_fd = os.open(self.paths.root, flags)
        except OSError as exc:
            raise FleetPlanInvalid("original private attempt root unavailable") from exc
        try:
            root_stat = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_stat.st_mode)
                or root_stat.st_uid != os.geteuid()
                or root_stat.st_mode & 0o077
            ):
                raise FleetPlanInvalid("Agent source attempt root is not private")
            # O_EXCL-like mkdir: a previous/partial stage can never be reused.
            os.mkdir("agent-source-prework", 0o700, dir_fd=root_fd)
            staging_fd = os.open("agent-source-prework", flags, dir_fd=root_fd)
            try:
                readback = []
                for tree in trees:
                    os.mkdir(tree.kind, 0o700, dir_fd=staging_fd)
                    tree_fd = os.open(tree.kind, flags, dir_fd=staging_fd)
                    try:
                        for member in tree.members:
                            parent_fd = os.dup(tree_fd)
                            try:
                                parts = member.path.split("/")
                                for component in parts[:-1]:
                                    try:
                                        os.mkdir(component, 0o700, dir_fd=parent_fd)
                                    except FileExistsError:
                                        pass
                                    next_fd = os.open(component, flags, dir_fd=parent_fd)
                                    os.close(parent_fd)
                                    parent_fd = next_fd
                                file_fd = os.open(
                                    parts[-1],
                                    os.O_WRONLY
                                    | os.O_CREAT
                                    | os.O_EXCL
                                    | os.O_NOFOLLOW
                                    | os.O_CLOEXEC,
                                    0o600,
                                    dir_fd=parent_fd,
                                )
                                try:
                                    with os.fdopen(file_fd, "wb", closefd=False) as stream:
                                        stream.write(member.data)
                                        stream.flush()
                                    os.fsync(file_fd)
                                finally:
                                    os.close(file_fd)
                                file_fd = os.open(
                                    parts[-1],
                                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=parent_fd,
                                )
                                try:
                                    info = os.fstat(file_fd)
                                    if (
                                        not stat.S_ISREG(info.st_mode)
                                        or info.st_nlink != 1
                                        or info.st_uid != os.geteuid()
                                    ):
                                        raise FleetPlanInvalid("Agent staged file custody invalid")
                                    with os.fdopen(file_fd, "rb", closefd=False) as reader:
                                        actual = reader.read(len(member.data) + 1)
                                    if (
                                        len(actual) != len(member.data)
                                        or hashlib.sha256(actual).hexdigest() != member.sha256
                                    ):
                                        raise FleetPlanInvalid(
                                            "Agent source stage readback mismatch"
                                        )
                                    # Seal the verified bytes before publishing any
                                    # prework receipt. This is discretionary custody,
                                    # NOT a kernel immutability guarantee.
                                    if require_kernel_file_verity:
                                        # Optional *explicit* positive file-byte custody.
                                        # No silent fallback on unsupported mounts.
                                        from oh_goon.integrations.fleet_plan_agent_fsverity import (  # noqa: PLC0415
                                            enable_original_agent_file_verity,
                                        )

                                        enable_original_agent_file_verity(file_fd)
                                    os.fchmod(file_fd, 0o400)
                                    os.fsync(file_fd)
                                finally:
                                    os.close(file_fd)
                                readback.append(
                                    {
                                        "kind": tree.kind,
                                        "path": member.path,
                                        "sha256": member.sha256,
                                        "bytes": len(member.data),
                                    }
                                )
                                os.fsync(parent_fd)
                            finally:
                                os.close(parent_fd)

                        # A nested directory stays writable until all of its
                        # members have been materialized. Seal descendants
                        # bottom-up so no writable subdirectory is left behind.
                        def seal_directories(directory_fd: int) -> None:
                            for name in sorted(os.listdir(directory_fd)):
                                entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                                if stat.S_ISDIR(entry.st_mode):
                                    child = os.open(name, flags, dir_fd=directory_fd)
                                    try:
                                        seal_directories(child)
                                    finally:
                                        os.close(child)
                                elif not stat.S_ISREG(entry.st_mode):
                                    raise FleetPlanInvalid("unsafe Agent stage member before seal")
                            os.fchmod(directory_fd, 0o500)
                            os.fsync(directory_fd)
                            if require_kernel_directory_immutable:
                                from oh_goon.integrations.fleet_plan_agent_dir_immutable import (  # noqa: PLC0415
                                    enable_original_agent_directory_immutable,
                                )

                                enable_original_agent_directory_immutable(directory_fd)

                        seal_directories(tree_fd)
                    finally:
                        os.close(tree_fd)
                os.fchmod(staging_fd, 0o500)
                os.fsync(staging_fd)
                if require_kernel_directory_immutable:
                    from oh_goon.integrations.fleet_plan_agent_dir_immutable import (  # noqa: PLC0415
                        enable_original_agent_directory_immutable,
                    )

                    enable_original_agent_directory_immutable(staging_fd)
                os.fsync(root_fd)
                # The opened inode must still be the caller's original attempt
                # root; a renamed/replaced path cannot inherit the receipt.
                root_now = os.stat(self.paths.root, follow_symlinks=False)
                if (root_now.st_dev, root_now.st_ino) != (root_stat.st_dev, root_stat.st_ino):
                    raise FleetPlanInvalid("Agent source attempt root changed during staging")
                return {
                    "schema": "oh-goon.agent-prework-attempt-stage/v1",
                    "handoff_identity": handoff.handoff_identity,
                    "stage_identity": digest(
                        "oh-goon/agent-prework-stage/v1", canonical_bytes({"members": readback})
                    ),
                    "file_count": len(readback),
                    "readback_verified": True,
                    "effect_permission": "NONE",
                    "physical_sandbox_proven": False,
                }
            except BaseException:
                if require_kernel_directory_immutable:
                    # Only this newly-created, unpublished attempt-local stage
                    # can be unsealed here. A partial failure cannot become a
                    # reusable stage or signed prework receipt. No deletion.
                    from oh_goon.integrations.fleet_plan_agent_dir_immutable import (  # noqa: PLC0415
                        rollback_unpublished_agent_immutable_stage,
                    )

                    try:
                        rollback_unpublished_agent_immutable_stage(staging_fd)
                    except Exception as rollback_exc:
                        raise FleetPlanInvalid(
                            "Agent source stage rollback unproven; privileged recovery required"
                        ) from rollback_exc
                raise
            finally:
                os.close(staging_fd)
        except OSError as exc:
            raise FleetPlanInvalid("Agent source prework staging failed closed") from exc
        finally:
            os.close(root_fd)

    def _inspect_agent_prework_readonly_mount(
        self, handoff: object, stage: dict[str, object]
    ) -> OriginalSandboxMount:
        """AG-02B2B3B: no-effect proposal for the ORIGINAL LACE mount intent.

        Read ONLY the original attempt-private stage, not the repository or
        skills source paths. Recheck every staged file and reject extras,
        symlinks, hardlinks, path/inode swaps and unknown directories. The
        resulting OriginalSandboxMount is NOT an accepted RESOURCE_INTENT,
        a mount operation, physical custody or permission to run an agent.
        A later original Fleet/LACE owner must include it in authenticated
        mount intent and independently verify real native mount observations.
        """
        from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
        from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount

        source = getattr(handoff, "source", None)
        if not callable(getattr(handoff, "verify", None)) or source is None:
            raise FleetPlanInvalid("original frozen Agent Step handoff required")
        handoff = cast(FrozenAgentStepHandoff, handoff)
        handoff.verify()
        trees = (source.repository, source.skill)
        expected: dict[str, tuple[int, str]] = {}
        directories: set[str] = set()
        for tree in trees:
            tree.verify()
            directories.add(tree.kind)
            for member in tree.members:
                key = tree.kind + "/" + member.path
                if key in expected:
                    raise FleetPlanInvalid("duplicate staged Agent source entry")
                expected[key] = (len(member.data), member.sha256)
                components = key.split("/")
                directories.update("/".join(components[:i]) for i in range(1, len(components)))
        if not expected or len(expected) > 8192:
            raise FleetPlanInvalid("unbounded Agent source mount proposal")
        manifest = [
            {
                "kind": key.split("/", 1)[0],
                "path": key.split("/", 1)[1],
                "sha256": value[1],
                "bytes": value[0],
            }
            for key, value in sorted(expected.items())
        ]
        # The older staging receipt was produced in repository,skill order,
        # not globally sorted. Preserve that exact signed identity.
        stage_manifest = [
            {"kind": tree.kind, "path": m.path, "sha256": m.sha256, "bytes": len(m.data)}
            for tree in trees
            for m in tree.members
        ]
        identity = digest(
            "oh-goon/agent-prework-stage/v1", canonical_bytes({"members": stage_manifest})
        )
        if (
            type(stage) is not dict
            or set(stage)
            != {
                "schema",
                "handoff_identity",
                "stage_identity",
                "file_count",
                "readback_verified",
                "effect_permission",
                "physical_sandbox_proven",
            }
            or stage["schema"] != "oh-goon.agent-prework-attempt-stage/v1"
            or stage["handoff_identity"] != handoff.handoff_identity
            or stage["stage_identity"] != identity
            or stage["file_count"] != len(expected)
            or stage["readback_verified"] is not True
            or stage["effect_permission"] != "NONE"
            or stage["physical_sandbox_proven"] is not False
        ):
            raise FleetPlanInvalid("untrusted Agent source stage receipt")

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        root = self.paths.root
        try:
            root_fd = os.open(root, flags)
        except OSError as exc:
            raise FleetPlanInvalid("Agent source attempt root unavailable") from exc
        try:
            initial_root = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(initial_root.st_mode)
                or initial_root.st_uid != os.geteuid()
                or initial_root.st_mode & 0o077
            ):
                raise FleetPlanInvalid("Agent source attempt root not private")
            stage_fd = os.open("agent-source-prework", flags, dir_fd=root_fd)
            try:
                initial_stage = os.fstat(stage_fd)
                seen: set[str] = set()
                found_dirs: set[str] = set()

                def visit(fd: int, prefix: str) -> None:
                    before = os.fstat(fd)
                    if (
                        not stat.S_ISDIR(before.st_mode)
                        or before.st_uid != os.geteuid()
                        or stat.S_IMODE(before.st_mode) != 0o500
                    ):
                        raise FleetPlanInvalid("Agent source directory seal invalid")
                    for name in sorted(os.listdir(fd)):
                        if name in {"", ".", ".."} or "/" in name or "\x00" in name:
                            raise FleetPlanInvalid("Agent source stage contains unsafe name")
                        rel = prefix + "/" + name if prefix else name
                        item_fd = os.open(
                            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                        )
                        try:
                            item = os.fstat(item_fd)
                            if stat.S_ISDIR(item.st_mode):
                                if rel not in directories:
                                    raise FleetPlanInvalid(
                                        "unexpected Agent source stage directory"
                                    )
                                found_dirs.add(rel)
                                visit(item_fd, rel)
                            elif stat.S_ISREG(item.st_mode):
                                if (
                                    rel not in expected
                                    or item.st_nlink != 1
                                    or item.st_uid != os.geteuid()
                                    or stat.S_IMODE(item.st_mode) != 0o400
                                ):
                                    raise FleetPlanInvalid("unexpected or unsafe Agent staged file")
                                count, sha = expected[rel]
                                with os.fdopen(item_fd, "rb", closefd=False) as stream:
                                    raw = stream.read(count + 1)
                                after = os.fstat(item_fd)
                                if (
                                    len(raw) != count
                                    or hashlib.sha256(raw).hexdigest() != sha
                                    or (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
                                    != (
                                        after.st_dev,
                                        after.st_ino,
                                        after.st_size,
                                        after.st_mtime_ns,
                                    )
                                ):
                                    raise FleetPlanInvalid(
                                        "Agent staged source changed since commitment"
                                    )
                                seen.add(rel)
                            else:
                                raise FleetPlanInvalid("nonregular Agent stage member")
                        finally:
                            os.close(item_fd)
                    after_dir = os.fstat(fd)
                    if (before.st_dev, before.st_ino, before.st_mtime_ns) != (
                        after_dir.st_dev,
                        after_dir.st_ino,
                        after_dir.st_mtime_ns,
                    ):
                        raise FleetPlanInvalid("Agent stage directory changed during readback")

                visit(stage_fd, "")
                if seen != set(expected) or found_dirs != directories:
                    raise FleetPlanInvalid("Agent source stage incomplete or extended")
                root_now = os.stat(root, follow_symlinks=False)
                stage_now = os.stat("agent-source-prework", dir_fd=root_fd, follow_symlinks=False)
                if (root_now.st_dev, root_now.st_ino) != (
                    initial_root.st_dev,
                    initial_root.st_ino,
                ) or (stage_now.st_dev, stage_now.st_ino) != (
                    initial_stage.st_dev,
                    initial_stage.st_ino,
                ):
                    raise FleetPlanInvalid("Agent source mount staging ancestry replaced")
                # An original declared mount, not an executable/signed resource
                # commitment. Never reinterpret this as physical containment.
                proposal_digest = hashlib.sha256(
                    canonical_bytes(
                        {
                            "handoff_identity": handoff.handoff_identity,
                            "stage_identity": identity,
                            "members": manifest,
                        }
                    )
                ).hexdigest()
                return OriginalSandboxMount(
                    source=str(root / "agent-source-prework"),
                    target="/opt/oh-goon/agent-source",
                    sha256=proposal_digest,
                )
            finally:
                os.close(stage_fd)
        except OSError as exc:
            raise FleetPlanInvalid("Agent source mount custody inspection failed closed") from exc
        finally:
            os.close(root_fd)


class PreparedPlanRun:
    """Own immutable run inputs, explicit lineage, and disposable CI/CD attempts."""

    def __init__(
        self,
        repository: RepositoryIdentity,
        plan: ResolvedFleetPlan,
        *,
        source_authority: RepositorySourceAuthority,
        process_isolation: ProcessIsolationBackend,
        isolation_backend: PlanIsolationBackendEvidence,
        path_authority: StrictHostPathAuthority,
        managed_runtimes: Mapping[str, ManagedRuntimeSpec] | None = None,
        content_store: FleetContentStore | None = None,
        run_parent: Path | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        trusted_local_consent: TrustedLocalRunConsent | None = None,
        trusted_local_scope: TrustedLocalRunScope | None = None,
    ) -> None:
        # These are original RUN_ADMITTED facts, not an effect-time permit.
        # A matching pair must travel to each physical Step request unchanged.
        if (trusted_local_consent is None) != (trusted_local_scope is None):
            raise FleetPlanInvalid("TL-1 prepared Run requires exact consent and scope together")
        if (
            process_isolation.capabilities.backend_id == "trusted-local"
            and trusted_local_consent is None
        ):
            raise FleetPlanInvalid("TL-1 prepared Run requires original journal consent")
        if (
            process_isolation.capabilities.backend_id != "trusted-local"
            and trusted_local_consent is not None
        ):
            raise FleetPlanInvalid("TL-1 consent cannot enter strict backend preparation")
        self.trusted_local_consent = trusted_local_consent
        self.trusted_local_scope = trusted_local_scope
        self.process_isolation = process_isolation
        # ``cancel_requested`` is preparation-only authority. It may be backed by a
        # bounded SQLite observer whose connection is closed as soon as preparation
        # returns. Never retain that callback into workload execution.
        self._cancel_requested: Callable[[], bool] | None = None
        self._maintenance_receipt: FleetContentMaintenanceReceipt | None = None
        if run_parent is not None:
            run_parent = ensure_private_directory(run_parent)
        self._temporary = tempfile.TemporaryDirectory(prefix="oh-goon-plan-", dir=run_parent)
        self.root = canonical_path(self._temporary.name)
        self._layout = RunPathLayout.from_root(self.root)
        try:
            self._inputs = _FleetPlanRunInputs(
                repository,
                self.root,
                plan,
                source_authority=source_authority,
                isolation_backend=isolation_backend,
                path_authority=path_authority,
                managed_runtimes=managed_runtimes,
                cancel_requested=cancel_requested,
            )
            active_steps = tuple(
                step for step in plan.steps if step.step_id not in self._inputs.import_skips
            )
            self._lineage = _RunLineage(
                self.root,
                self._inputs,
                active_steps,
                cancel_requested,
                content_store,
                repository.repository_id,
            )
            self._execution_inputs = self._inputs.evidence(
                content_inputs=self._lineage.content_input_evidence(),
            )
            self._lineage.clear_preparation_cancellation_observation()
        except BaseException:
            self._temporary.cleanup()
            raise

    def bind_execution_cancel_requested(self, cancel_requested: Callable[[], bool] | None) -> None:
        """Bind the live execution callback after preparation observer teardown.

        Strict preparation can use a short-lived persistent SQLite observer. That
        callback is invalid once preparation returns, while physical attempts, cache
        publication and lineage work continue during execution. Rebinding is therefore
        an explicit lifetime boundary rather than allowing preparation authority to
        escape inside retained prepared resources.
        """

        self._cancel_requested = cancel_requested
        self._lineage.bind_execution_cancel_requested(cancel_requested)

    @property
    def repository_id(self) -> str:
        return self._inputs.repository_id

    @property
    def plan_id(self) -> str:
        return self._inputs.plan_id

    @property
    def content_store(self) -> FleetContentStore | None:
        return self._lineage.content_store

    def retained_content_path(
        self,
        path: Path,
        *,
        owned_root: Path,
        label: str,
    ) -> RepositoryScopedPath:
        run_space = RepositoryPathSpace.from_root(self.root)
        run_space.relative(owned_root, label=f"{label} ownership root")
        return RepositoryScopedPath.create(
            owner_repository_id=self.repository_id,
            root=owned_root,
            path=path,
            label=label,
        )

    @property
    def host_environment(self) -> Mapping[str, str]:
        return self._inputs.host_environment

    @property
    def base_workspace(self) -> Path:
        """Frozen repository snapshot used for admission and imported-Goon applicability."""
        return self._inputs.base_workspace

    def template_import_skip_reason(self, step_id: str) -> str | None:
        """Return frozen imported-Goon applicability for one concrete step."""
        return self._inputs.import_skips.get(step_id)

    @property
    def maintenance_receipt(self) -> FleetContentMaintenanceReceipt | None:
        return self._maintenance_receipt

    def close(self) -> None:
        if self._maintenance_receipt is None and self.content_store is not None:
            self._maintenance_receipt = self.content_store.maintain_retained_content()
        self._temporary.cleanup()

    def __enter__(self) -> PreparedPlanRun:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def source_for(self, step: ResolvedPlanStep) -> Path:
        """Return the frozen source view for read-only observation.

        Physical attempt creation owns the mutation proof: it snapshots this source
        into the attempt workspace and compares that copied identity with the identity
        bound during run preparation.  Re-hashing here would create a second,
        redundant mutation gate before the same copy proof.
        """
        path, _identity = self._lineage.source(step)
        return path

    def _create_physical_attempt(
        self, step: ResolvedPlanStep, attempt_number: int
    ) -> FleetPlanStepAttempt:
        source, source_identity = self._lineage.source(step)
        attempt_root = self._layout.attempt_root(step.step_id, attempt_number)
        if attempt_root.exists() or attempt_root.is_symlink():
            raise FleetPlanInvalid(
                f"Fleet physical attempt already exists: {step.step_id} attempt {attempt_number}"
            )
        attempt_root.mkdir(parents=True)
        attempt_layout = AttemptPathLayout.from_root(attempt_root)
        workspace = attempt_layout.workspace
        copied_identity = copy_frozen_directory(
            source,
            workspace,
            label=f"Fleet step {step.step_id} source workspace",
            cancel_requested=self._cancel_requested,
        )
        if copied_identity != source_identity:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} source changed while creating attempt {attempt_number}"
            )
        home, tmp, cache, config, etc = (
            attempt_layout.home,
            attempt_layout.tmp,
            attempt_layout.cache,
            attempt_layout.config,
            attempt_layout.etc,
        )
        for path in (home, tmp, config, etc):
            path.mkdir()
        AttemptWorkspaceAuthority.create(
            root=attempt_root,
            workspace=workspace,
            home=home,
            tmp=tmp,
            cache=cache,
            config=config,
            etc=etc,
        )
        binding = self._inputs.step_binding(step)
        cache_identity = self._lineage.seed_cache(step, cache)
        if isinstance(binding.managed_runtime, UvProjectManagedRuntime):
            cache_identity = _seed_uv_prepared_cache(
                cache,
                binding.managed_runtime,
                binding.sandbox_bindings,
                cancel_requested=self._cancel_requested,
            )
        read_only_cache = self._lineage.read_only_cache_binding(step)
        sandbox_bindings = binding.sandbox_bindings + (
            (read_only_cache,) if read_only_cache else ()
        )
        _write_minimal_etc(etc, hosts=binding.hosts)
        _clear_declared_publish_channels(workspace, step)
        host_tools = self._inputs.host_tools_for(step)
        executable_materialization = bind_step_executable_materialization(
            workspace,
            step,
            binding.execution_authority,
            allowed_host_tools=tuple(item.name for item in host_tools),
        )
        return FleetPlanStepAttempt(
            paths=attempt_layout,
            cache_identity=cache_identity,
            egress=binding.egress,
            host_tools=host_tools,
            sandbox_bindings=sandbox_bindings,
            managed_runtime=binding.managed_runtime,
            host_environment=self._inputs.host_environment_for(step),
            runtime_root=self._inputs.runtime_root,
            isolation_backend=self._inputs.isolation_backend,
            resources=self._inputs.resources,
            nested_strict_execution=step.capabilities.nested_strict_execution,
            executable_materialization=executable_materialization,
            attempt_number=attempt_number,
        )

    def create_attempt(self, step: ResolvedPlanStep, attempt_number: int) -> FleetPlanStepAttempt:
        """Create the physical attempt directly; run authority stays with this owner."""
        return self._create_physical_attempt(step, attempt_number)

    def publish_workspace(self, step: ResolvedPlanStep, workspace: Path) -> None:
        self._lineage.publish_workspace(step, workspace)

    def publish_cache(
        self, step: ResolvedPlanStep, cache: Path
    ) -> tuple[SnapshotIdentity | None, FleetContentObject | None]:
        return self._lineage.publish_cache(step, cache)

    def discard_publications(self, step: ResolvedPlanStep) -> None:
        """Roll back run-local workspace/cache publications for one failed attempt."""

        self._lineage.discard_publications(step)

    def release_step(self, step: ResolvedPlanStep) -> PlanStepTeardownEvidence:
        """Release one active step and return bounded proof of attempt-private root cleanup."""

        teardown = _teardown_step_attempts(self._layout, step.step_id)
        if self.template_import_skip_reason(step.step_id) is None:
            self._lineage.release(step)
        return teardown

    def execution_input_evidence(self) -> PlanExecutionInputEvidence:
        """Return the one immutable execution-input fact frozen during preparation."""
        return self._execution_inputs


def _seed_uv_prepared_cache(
    cache: Path,
    runtime: UvProjectManagedRuntime,
    bindings: tuple[SnapshotBinding, ...],
    *,
    cancel_requested: Callable[[], bool] | None,
) -> SnapshotIdentity:
    binding = next(
        (
            item
            for item in bindings
            if canonical_path(item.declared_path) == canonical_path(runtime.cache)
        ),
        None,
    )
    if binding is None:
        raise FleetPlanInvalid("Strict uv execution is missing its prepared native cache binding")
    destination = cache / "uv"
    observed = copy_frozen_directory(
        binding.snapshot_path,
        destination,
        label="Fleet strict uv prepared cache",
        cancel_requested=cancel_requested,
    )
    if observed != binding.identity:
        raise FleetPlanInvalid("Prepared native uv cache changed while seeding physical attempt")
    return snapshot_identity(cache, cancel_requested=cancel_requested)


_CACHE_EXECUTION_RUNTIME_MATERIALIZATION_ROOTS: tuple[PurePosixPath, ...] = (
    PurePosixPath("uv-project-environment"),
)


def _materialize_generated_runtime_links(attempt: FleetPlanStepAttempt) -> None:
    """Seal sandbox-only runtime links under explicit generated execution roots.

    A strict Step may create virtualenv interpreter links that name the sandbox
    runtime namespace (for example ``/usr/bin/python3``). On the host that pathname
    is outside the attempt-owned workspace/cache, but it is not ambient authority:
    the sandbox saw Fleet's frozen runtime root. Before host-side evidence/publication,
    replace such links with bytes copied from that frozen authority. Only the explicit
    generated virtualenv roots are traversed; arbitrary workspace/cache symlinks still
    fail owned-tree sealing.
    """

    _materialize_generated_runtime_links_under(
        attempt,
        host_root=attempt.paths.workspace.resolve(),
        sandbox_root=SANDBOX_PATHS.workspace,
        relative_roots=EXECUTION_RUNTIME_MATERIALIZATION_ROOTS,
    )
    _materialize_generated_runtime_links_under(
        attempt,
        host_root=attempt.paths.cache.resolve(),
        sandbox_root=SANDBOX_PATHS.cache,
        relative_roots=_CACHE_EXECUTION_RUNTIME_MATERIALIZATION_ROOTS,
    )


def _materialize_generated_runtime_links_under(
    attempt: FleetPlanStepAttempt,
    *,
    host_root: Path,
    sandbox_root: PurePosixPath,
    relative_roots: tuple[PurePosixPath, ...],
) -> None:
    for relative_root in relative_roots:
        generated_root = host_root / Path(*relative_root.parts)
        if not generated_root.is_dir() or generated_root.is_symlink():
            continue
        for current, directories, files in os.walk(generated_root, topdown=True, followlinks=False):
            directories.sort()
            files.sort()
            current_path = Path(current)
            for name in (*directories, *files):
                entry = current_path / name
                if not entry.is_symlink():
                    continue
                sandbox_target = _external_sandbox_target(
                    entry, host_root=host_root, sandbox_root=sandbox_root
                )
                if sandbox_target is None:
                    continue
                source = _frozen_sandbox_source(attempt, sandbox_target)
                if source is None:
                    relative = entry.relative_to(host_root)
                    raise FleetPlanInvalid(
                        "Fleet generated runtime symlink does not resolve to declared frozen "
                        f"sandbox authority: {sandbox_root / PurePosixPath(*relative.parts)} "
                        f"-> {sandbox_target}"
                    )
                relative = entry.relative_to(host_root)
                _replace_link_with_frozen_source(
                    entry,
                    source,
                    label=(
                        "Fleet generated runtime link "
                        f"{sandbox_root / PurePosixPath(*relative.parts)}"
                    ),
                )


def _external_sandbox_target(
    entry: Path, *, host_root: Path, sandbox_root: PurePosixPath
) -> PurePosixPath | None:
    """Resolve one generated link chain in sandbox semantics without live host targets."""

    current = sandbox_root / PurePosixPath(*entry.relative_to(host_root).parts)
    observed: set[PurePosixPath] = set()
    while current == sandbox_root or current.is_relative_to(sandbox_root):
        if current in observed:
            raise FleetPlanInvalid(
                "Fleet generated runtime symlink cycle: "
                f"{sandbox_root / PurePosixPath(*entry.relative_to(host_root).parts)}"
            )
        observed.add(current)
        relative = current.relative_to(sandbox_root)
        host_path = host_root / Path(*relative.parts)
        if not host_path.is_symlink():
            return None
        raw = os.readlink(host_path)
        if raw.startswith("/"):
            current = PurePosixPath(posixpath.normpath(raw))
        else:
            current = PurePosixPath(posixpath.normpath(str(current.parent / raw)))
    return current


def _frozen_sandbox_source(attempt: FleetPlanStepAttempt, target: PurePosixPath) -> Path | None:
    """Map one sandbox pathname back to the immutable Fleet input that owned it."""

    toolchain = PurePosixPath("/toolchain/bin")
    for binding in attempt.host_tools:
        sandbox_path = toolchain / binding.name
        if target == sandbox_path or (binding.name == "sh" and target == PurePosixPath("/bin/sh")):
            return binding.execution_snapshot_path
    for sandbox_binding in attempt.sandbox_bindings:
        sandbox_root = sandbox_binding.sandbox_path
        if sandbox_root is None:
            continue
        if target == sandbox_root:
            return sandbox_binding.snapshot_path
        if target.is_relative_to(sandbox_root) and sandbox_binding.snapshot_path.is_dir():
            relative = target.relative_to(sandbox_root)
            return sandbox_binding.snapshot_path / Path(*relative.parts)
    reserved = (
        PurePosixPath("/workspace"),
        PurePosixPath("/cache"),
        PurePosixPath("/home/fleet"),
        PurePosixPath("/tmp"),
        PurePosixPath("/config"),
        PurePosixPath("/managed"),
        PurePosixPath("/toolchain/bin"),
    )
    if any(target == root or target.is_relative_to(root) for root in reserved):
        return None
    if not target.is_absolute():
        return None
    candidate = attempt.runtime_root.root / Path(*target.parts[1:])
    return candidate if candidate.exists() else None


def _replace_link_with_frozen_source(entry: Path, source: Path, *, label: str) -> None:
    """Replace one symlink with a self-contained copy of its frozen target."""

    staging = Path(tempfile.mkdtemp(prefix=".fleet-runtime-link-", dir=entry.parent))
    staging.rmdir()
    try:
        if source.is_file() and not source.is_symlink():
            snapshot_file(source, staging, label=label)
        elif source.is_dir():
            snapshot_directory_dereferenced(source, staging, label=label)
        else:
            raise FleetPlanInvalid(
                f"{label} target is not a frozen regular file/directory: {source}"
            )
        entry.unlink()
        staging.replace(entry)
    finally:
        if staging.exists():
            if staging.is_dir() and not staging.is_symlink():
                shutil.rmtree(staging)
            else:
                staging.unlink(missing_ok=True)


def _prepare_private_tree_for_removal(root: Path) -> None:
    """Restore owner delete authority only inside one disposable Fleet attempt tree.

    Strict steps may legitimately materialize immutable/read-only dependency directories.
    Those modes must not turn Fleet's own private attempt cleanup into a false execution
    failure.  We never follow symlinks and never chmod outside the attempt root.
    """

    for current, directories, _files in os.walk(root, topdown=True, followlinks=False):
        candidates = [Path(current), *(Path(current) / name for name in directories)]
        for candidate in candidates:
            try:
                metadata = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                continue
            os.chmod(
                candidate,
                stat.S_IMODE(metadata.st_mode) | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
                follow_symlinks=False,
            )


def _teardown_step_attempts(layout: RunPathLayout, step_id: str) -> PlanStepTeardownEvidence:
    """Remove only the path-authority-owned attempt root and verify its absence."""

    root = layout.step_attempts_root(step_id)
    if not root.exists():
        return CompletedPlanStepTeardownEvidence(attempt_roots_observed=0)
    try:
        observed = sum(1 for _ in root.iterdir())
    except OSError:
        return FailedPlanStepTeardownEvidence(
            attempt_roots_observed=0,
            reason_code="INSPECTION_FAILED",
        )
    try:
        _prepare_private_tree_for_removal(root)
        shutil.rmtree(root)
    except OSError:
        return FailedPlanStepTeardownEvidence(
            attempt_roots_observed=observed,
            reason_code="REMOVAL_FAILED",
        )
    if root.exists():
        return FailedPlanStepTeardownEvidence(
            attempt_roots_observed=observed,
            reason_code="ROOT_REMAINS",
        )
    return CompletedPlanStepTeardownEvidence(attempt_roots_observed=observed)


def _clear_declared_publish_channels(workspace: Path, step: ResolvedPlanStep) -> None:
    """Declared outputs/artifacts are produced by this attempt, never inherited evidence."""

    paths = RepositoryPathSpace.from_root(workspace)
    declared = [
        output.path
        for output in getattr(step, "outputs", ())
        if not isinstance(output, PlanStreamOutput)
    ]
    declared.extend(artifact.path for artifact in getattr(step, "artifacts", ()))
    for value in declared:
        candidate = paths.resolve(value, label="Fleet plan publish path")
        if candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate)
        else:
            candidate.unlink(missing_ok=True)


def _write_minimal_etc(etc: Path, *, hosts: tuple[tuple[str, str], ...] = ()) -> None:
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    (etc / "passwd").write_text(
        f"fleet:x:{uid}:{gid}:Fleet sandbox:/home/fleet:/bin/sh\n", encoding="utf-8"
    )
    (etc / "group").write_text(f"fleet:x:{gid}:\n", encoding="utf-8")
    nsswitch = "hosts: files\n" if hosts else "hosts: files dns\n"
    (etc / "nsswitch.conf").write_text(nsswitch, encoding="utf-8")
    host_lines = [
        "127.0.0.1 localhost",
        "::1 localhost ip6-localhost ip6-loopback",
        *(f"{address} {hostname}" for hostname, address in hosts),
    ]
    (etc / "hosts").write_text("\n".join(host_lines) + "\n", encoding="utf-8")
