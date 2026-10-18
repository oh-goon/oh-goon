from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.path_layouts import SANDBOX_PATHS
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import (
    CommandOutputObserver,
    CommandProcessObserver,
    CommandResult,
    run_command,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.integrations._fleet_plan_cgroup_resources import (
    NESTED_CGROUP_DELEGATED_ENV,
    NESTED_CGROUP_PROCESS_SUBGROUP,
    NESTED_CGROUP_SUBGROUP_ENV,
    CgroupV2AttemptLease,
    cgroup_configuration_identity,
    require_cgroup_v2_host,
)
from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
from oh_goon.integrations._fleet_plan_restricted_egress import (
    RestrictedEgressLease,
    require_restricted_egress_host,
)
from oh_goon.integrations._fleet_plan_runtime_root import verify_runtime_root
from oh_goon.integrations._fleet_plan_sandbox_launch import sandbox_process_launch
from oh_goon.integrations.content_snapshot import verify_snapshot
from oh_goon.integrations.fleet_plan_atomic_runtime_authority import (
    AtomicRuntimeAuthorityLease,
    bind_atomic_runtime_authority,
)
from oh_goon.integrations.fleet_plan_bind_source_custody import (
    OriginalBindSourceLease,
    require_bubblewrap_fd_binding,
)
from oh_goon.integrations.fleet_plan_local_lace_allocation import observe_local_allocation
from oh_goon.integrations.fleet_plan_local_lace_intent import (
    local_resource_identities,
    prepared_original_native_mounts,
    require_original_agent_source_mount_authority,
)
from oh_goon.integrations.fleet_plan_local_lace_recovery import lace_resource_binding
from oh_goon.integrations.fleet_plan_process_isolation import (
    MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
    IsolatedProcessRequest,
    verify_selected_backend,
)
from oh_goon.integrations.source_snapshot import verify_clean_source_snapshot


class BubblewrapProcessIsolation:
    """Reference local backend: one strict process boundary on the shared host kernel."""

    capabilities = IsolationBackendCapabilities(
        backend_id="bubblewrap",
        private_filesystem=True,
        read_only_workspace=True,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=True,
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
        remote_execution=False,
    )

    def __init__(
        self,
        executable: str,
        *,
        ip_executable: str | None = None,
        nft_executable: str | None = None,
        egress_pool: str | None = None,
        control_state_root: Path | None = None,
        egress_lock: Path | None = None,
        egress_state_dir: Path | None = None,
        cgroup_root: Path | None = None,
        cgroup_lock: Path | None = None,
        cgroup_state_dir: Path | None = None,
        require_local_resource_intent: bool = False,
    ) -> None:
        self.executable = executable
        self.require_local_resource_intent = require_local_resource_intent
        self.ip_executable = ip_executable
        self.nft_executable = nft_executable
        self.egress_pool = egress_pool
        control_root = (
            control_state_root or Path.home() / ".local" / "share" / "oh-goon" / "executor-control"
        )
        self.egress_lock = egress_lock or control_root / "egress.lock"
        self.egress_state_dir = egress_state_dir or control_root / "egress"
        self.cgroup_root = cgroup_root
        self.cgroup_lock = cgroup_lock or control_root / "cgroups.lock"
        self.cgroup_state_dir = cgroup_state_dir or control_root / "cgroups"

    def configuration_identity(self) -> str:
        return stable_id(
            "process-isolation-backend",
            {
                "backend": self.capabilities.backend_id,
                "requires_local_resource_intent": self.require_local_resource_intent,
                "bwrap_sha256": _executable_sha256(self.executable),
                "ip_sha256": _executable_sha256(self.ip_executable),
                "nft_sha256": _executable_sha256(self.nft_executable),
                "egress_pool": self.egress_pool,
                "resources": cgroup_configuration_identity(self),
            },
        )

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        if os.name == "nt" or not self.executable or not Path(self.executable).is_file():
            raise FleetPlanInvalid(
                "Strict Fleet CI/CD isolation requires the bubblewrap (bwrap) backend on this host; "
                "execution is denied rather than downgraded to process-only isolation"
            )
        execution = plan.execution
        if not isinstance(execution, CicdExecutionPolicy):
            raise FleetPlanInvalid(
                "Bubblewrap strict resource enforcement is only valid for CI/CD plans"
            )
        require_cgroup_v2_host(self, execution.resources)
        if any(
            step.capabilities.egress.mode in {"cidr_allowlist", "hostname_allowlist"}
            for step in plan.steps
        ):
            require_restricted_egress_host(self)

    def execute(
        self,
        request: IsolatedProcessRequest,
        *,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
    ) -> CommandResult:
        require_original_agent_source_mount_authority(request)
        verify_selected_backend(self, request)
        proof: LocalLaceIntentProof | None = None
        # Optional original LACE admission is an explicit None, never an unbound proof.
        # Strict mode still rejects before physical allocation when it is missing.
        # Strict opt-in: journal commit precedes cgroup/native network/mount effects.
        # No synthetic intent, implicit admission, or fallback when required.
        if self.require_local_resource_intent and request.local_resource_intent is None:
            raise FleetPlanInvalid(
                "authenticated local LACE RESOURCE_INTENT missing before physical work"
            )
        if request.local_resource_intent is not None:
            if not callable(getattr(request.local_resource_intent, "reserve", None)):
                raise FleetPlanInvalid("original local LACE reservation owner required")
            proof = request.local_resource_intent.reserve(request, self)
            if (
                type(proof) is not LocalLaceIntentProof
                or proof.run_id != request.fleet_run_id
                or proof.step_id != request.step_id
                or proof.command_ordinal != request.command_ordinal
                or type(proof.run_seq) is not int
                or proof.run_seq < 2
                or type(proof.attempt_identity) is not str
                or not proof.attempt_identity.startswith("sha256:")
                or len(proof.attempt_identity) != 71
                or type(proof.mounts_identity) is not str
                or not proof.mounts_identity.startswith("sha256:")
                or len(proof.mounts_identity) != 71
            ):
                raise FleetPlanInvalid("local LACE journal did not return exact committed intent")
            _require_original_signed_mount_identity(request, proof.mounts_identity)
        if self.require_local_resource_intent and not callable(
            getattr(request.local_resource_intent, "observe", None)
        ):
            raise FleetPlanInvalid(
                "original local LACE allocation recorder required before physical work"
            )
        if self.require_local_resource_intent and not callable(
            getattr(request.local_resource_intent, "require_effect_authority", None)
        ):
            raise FleetPlanInvalid("original LACE pre-effect revocation fence missing")

        def require_effect_authority() -> None:
            if self.require_local_resource_intent:
                # One original Fleet owner, one signed intent. Revalidation is
                # not a new admission or permission minted from a local scan.
                request.local_resource_intent.require_effect_authority(proof)

        termination_requested = request.termination_requested
        effective_cancel_requested = cancel_requested
        if termination_requested is not None:

            def effective_cancel_requested() -> bool:
                if termination_requested() is not None:
                    return True
                return cancel_requested is not None and cancel_requested()

        # Pin EVERY prepared source before cgroup/network allocation; no late
        # pathname resolution by Bubblewrap and no unsupported-binary fallback.
        require_bubblewrap_fd_binding(self.executable)
        with OriginalBindSourceLease.pin_prepared(request) as mount_source_lease:
            resources = request.attempt.resources
            # Recheck the ONE original signed Enforcer reservation before
            # invoking native owners. A prior reserve is not an effect-time
            # grant; stale epochs must reject before LACE admission/work.
            require_effect_authority()
            resource_lease = CgroupV2AttemptLease.create(
                self,
                request.attempt.paths.root,
                resources,
                nested_strict_execution=request.attempt.nested_strict_execution,
            )
            require_effect_authority()
            network_lease = RestrictedEgressLease.create(
                self,
                request.attempt.paths.root,
                request.attempt.egress,
            )
            if self.require_local_resource_intent:
                binding = lace_resource_binding(proof)
                resource_lease.lace_intent = binding
                if network_lease is not None:
                    network_lease.lace_intent = binding
            require_effect_authority()
            resource_lease.open()
            try:
                if network_lease is not None:
                    require_effect_authority()
                    network_lease.open(cancel_requested=effective_cancel_requested)
                if self.require_local_resource_intent:
                    # The collector reads original cgroup and native network owner
                    # state; it cannot accept workload-supplied resource names.
                    observed = observe_local_allocation(
                        resource_lease, network_lease, request, proof=proof
                    )
                    request.local_resource_intent.observe(proof, observed)
                if request.attempt.nested_strict_execution:
                    mount_source_lease.pin_dynamic_cgroup(resource_lease.nested_delegation)
                mount_source_lease.verify_kernel_custody()
                launch = sandbox_process_launch(request)
                with bind_atomic_runtime_authority(request, launch) as atomic_authority:
                    atomic_authority.verify_kernel_custody()
                    argv, env = self._command(
                        request,
                        launch=launch,
                        atomic_authority=atomic_authority,
                        network_prefix=network_lease.command_prefix() if network_lease else (),
                        signed_mounts_identity=proof.mounts_identity if proof is not None else None,
                        mount_source_lease=mount_source_lease,
                        nested_cgroup_root=(
                            resource_lease.nested_delegation
                            if request.attempt.nested_strict_execution
                            else None
                        ),
                    )
                    atomic_authority.rewind_for_bubblewrap()
                    # One last original-lease check immediately before process
                    # handoff. This is not a claim about kernel mount teardown.
                    inherited_fds = _require_original_native_fd_handoff(
                        argv,
                        atomic_authority,
                        mount_source_lease,
                    )
                    require_effect_authority()
                    result = run_command(
                        *resource_lease.command_prefix(),
                        *argv,
                        timeout=timeout,
                        cwd=None,
                        env=env,
                        cancel_requested=effective_cancel_requested,
                        termination_requested=termination_requested,
                        stream_output=stream_output,
                        process_observer=process_observer,
                        runtime_supervision=runtime_supervision,
                        pass_fds=inherited_fds,
                        max_capture_bytes_per_stream=MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
                    )
                    # This confirms only this process's original bind-FD release.
                    # It is not unmounting, LACE teardown, or an Agent execution grant.
                    mount_source_lease.release_pins_after_process()
                    return replace(
                        result,
                        runtime_authority=atomic_authority.receipt,
                        execution_authority_economics=(
                            (atomic_authority.economics,)
                            if atomic_authority.economics is not None
                            else ()
                        ),
                    )
            finally:
                _close_local_resource_leases(network_lease, resource_lease)

    def _command(
        self,
        request: IsolatedProcessRequest,
        *,
        launch,
        atomic_authority: AtomicRuntimeAuthorityLease,
        network_prefix: tuple[str, ...],
        nested_cgroup_root: Path | None = None,
        signed_mounts_identity: str | None = None,
        mount_source_lease: OriginalBindSourceLease | None = None,
    ) -> tuple[tuple[str, ...], dict[str, str]]:
        args = _prepare_mounts(
            self.executable,
            request,
            atomic_authority=atomic_authority,
            network_prefix=network_prefix,
            nested_cgroup_root=nested_cgroup_root,
            signed_mounts_identity=signed_mounts_identity,
            mount_source_lease=mount_source_lease,
        )
        args.extend(("--chdir", str(launch.cwd), "--", *launch.argv))
        env = dict(launch.env)
        if nested_cgroup_root is not None:
            env[NESTED_CGROUP_DELEGATED_ENV] = "1"
            env[NESTED_CGROUP_SUBGROUP_ENV] = NESTED_CGROUP_PROCESS_SUBGROUP
        return tuple(args), env


def _require_original_native_fd_handoff(
    argv: tuple[str, ...],
    atomic_authority: AtomicRuntimeAuthorityLease,
    mount_source_lease: OriginalBindSourceLease,
) -> tuple[int, ...]:
    """One pre-spawn FD inheritance roster from both ORIGINAL live leases.

    No independently reassembled mount authority: the pinned bind lease checks
    the exact original Bubblewrap argv while the sealed runtime lease checks
    the admitted executable/interpreter bytes. Reject descriptor number aliasing
    across the two owners *before* run_command (which otherwise deduplicates
    inherited descriptors). Real mount consumption and teardown remain unproven.
    """
    # This is the completed Bubblewrap argv, unlike the pre-command mount
    # builder. A lost separator must not let Step-controlled tokens count as
    # mount options or otherwise impersonate the original signed FD roster.
    if "--" not in argv or argv.index("--") == 0 or argv.index("--") == len(argv) - 1:
        raise FleetPlanInvalid("Original Bubblewrap child-command boundary missing")
    atomic_authority.verify_kernel_custody()
    mount_source_lease.verify_fd_argv(list(argv))
    atomic_fds = atomic_authority.pass_fds
    mount_fds = mount_source_lease.pass_fds
    inherited_fds = (*atomic_fds, *mount_fds)
    if (
        not atomic_fds
        or not mount_fds
        or any(type(fd) is not int or fd < 3 for fd in inherited_fds)
        or len(set(inherited_fds)) != len(inherited_fds)
    ):
        raise FleetPlanInvalid("Original sealed and native bind FD custody overlaps or is invalid")
    return inherited_fds


def _close_local_resource_leases(
    network_lease: RestrictedEgressLease | None,
    resource_lease: CgroupV2AttemptLease,
) -> None:
    """Try both native teardown owners even if the first one fails.

    A failed network release must never suppress cgroup killing and process
    quiescence. Failures retain their respective durable recovery records.
    """
    failures: list[BaseException] = []
    for lease in (network_lease, resource_lease):
        if lease is None:
            continue
        try:
            lease.close()
        except BaseException as exc:
            failures.append(exc)
    if len(failures) > 1:
        raise BaseExceptionGroup("Bubblewrap local physical teardown UNPROVEN", failures)
    if failures:
        raise failures[0]


def _require_original_signed_mount_identity(
    request: IsolatedProcessRequest,
    signed_mounts_identity: str,
) -> None:
    """Consume one original signed mount meaning, never infer mount authority.

    Covers exact managed sources/targets and frozen host tools. This is not
    independent kernel mount or teardown proof. Agent source remains denied by
    require_original_agent_source_mount_authority at both effect boundaries.
    """
    if (
        type(signed_mounts_identity) is not str
        or local_resource_identities(request)["mounts"] != signed_mounts_identity
    ):
        raise FleetPlanInvalid(
            "Bubblewrap mount sources differ from original signed RESOURCE_INTENT"
        )


def _prepare_mounts(
    executable: str,
    request: IsolatedProcessRequest,
    *,
    atomic_authority: AtomicRuntimeAuthorityLease,
    network_prefix: tuple[str, ...],
    nested_cgroup_root: Path | None = None,
    signed_mounts_identity: str | None = None,
    mount_source_lease: OriginalBindSourceLease | None = None,
) -> list[str]:
    require_original_agent_source_mount_authority(request)
    # A native reservation may not silently downgrade its signed mount identity
    # to the legacy optional path if a caller drops the forwarding argument.
    # Both the original RESOURCE_INTENT and the final mount builder consume
    # that exact identity; the compiler must not re-infer it from the request.
    if request.local_resource_intent is not None and signed_mounts_identity is None:
        raise FleetPlanInvalid("Original signed RESOURCE_INTENT mount identity required at launch")
    if signed_mounts_identity is not None:
        _require_original_signed_mount_identity(request, signed_mounts_identity)
    verify_runtime_root(request.attempt.runtime_root)
    args = [
        *network_prefix,
        *_bubblewrap_prefix(
            executable,
            egress_mode=request.attempt.egress.mode,
            require_cgroup_namespace=nested_cgroup_root is not None,
        ),
    ]
    args.extend(("--ro-bind", str(request.attempt.runtime_root.root), "/"))
    if nested_cgroup_root is not None:
        args.extend(("--bind", str(nested_cgroup_root), "/sys/fs/cgroup"))
    args.extend(("--proc", str(SANDBOX_PATHS.proc), "--dev", str(SANDBOX_PATHS.dev)))
    attempt = request.attempt
    for name in ("passwd", "group", "nsswitch.conf", "hosts"):
        args.extend(("--ro-bind", str(attempt.paths.etc / name), str(SANDBOX_PATHS.etc / name)))
    if request.read_only_workspace is None:
        args.extend(("--bind", str(attempt.paths.workspace), str(SANDBOX_PATHS.workspace)))
    else:
        verify_clean_source_snapshot(
            request.read_only_workspace,
            label="Bubblewrap read-only execution workspace",
        )
        args.extend(
            (
                "--ro-bind",
                str(request.read_only_workspace.snapshot_path),
                str(SANDBOX_PATHS.workspace),
            )
        )
    args.extend(("--bind", str(attempt.paths.home), str(SANDBOX_PATHS.home)))
    args.extend(("--bind", str(attempt.paths.tmp), str(SANDBOX_PATHS.tmp)))
    if not any(binding.sandbox_path == SANDBOX_PATHS.cache for binding in attempt.sandbox_bindings):
        args.extend(("--bind", str(attempt.paths.cache), str(SANDBOX_PATHS.cache)))
    args.extend(("--bind", str(attempt.paths.config), str(SANDBOX_PATHS.config)))
    if getattr(request, "agent_source_mount", None) is not None:
        mount = getattr(request, "agent_source_mount", None)
        # Physical entry still refuses this path; once native custody is
        # qualified, it must be the SAME source/target/mode as signed intent.
        args.extend(("--ro-bind", mount.source, mount.target))
    _append_managed_inputs(args, request)
    _append_toolchain(args, request.attempt.host_tools)
    _append_atomic_execution_mounts(args, atomic_authority)
    # P1/B27B1: validate the *complete* mount argv against original prepared
    # sources and exact sealed FD descriptors before starting Bubblewrap. This
    # is a local prelaunch invariant, NOT a kernel inventory/teardown proof.
    _require_original_native_mount_argv(args, request, atomic_authority, nested_cgroup_root)
    if mount_source_lease is not None:
        mount_source_lease.consume_validated_argv(args)
    return args


def _require_original_native_mount_argv(
    args: list[str],
    request: IsolatedProcessRequest,
    atomic_authority: AtomicRuntimeAuthorityLease,
    nested_cgroup_root: Path | None,
) -> None:
    """Prevent an omitted/substituted mount in the ONE existing bwrap builder.

    Compare *all* effectful bind/data mounts, not merely the managed-input
    subset in the original signed mount descriptor. The runtime/root and
    writable source paths are already bound by original RESOURCE_INTENT's
    runtime/workspace identities. This proves argv correspondence only: native
    namespace, mount-FD custody and post-teardown absence remain UNVERIFIED.
    """
    attempt = request.attempt
    # The same prepared roster committed by original RESOURCE_INTENT, not a
    # second independently assembled list of trusted native mount sources.
    expected: list[tuple[str, str, str]] = [
        (mount.mode, mount.source, mount.target)
        for mount in prepared_original_native_mounts(request)
    ]
    if nested_cgroup_root is not None:
        expected.insert(1, ("--bind", str(nested_cgroup_root), "/sys/fs/cgroup"))
    expected += [
        ("--ro-bind", str(b.snapshot_path), str(b.sandbox_path)) for b in attempt.sandbox_bindings
    ]
    for t in attempt.host_tools:
        expected.append(
            ("--ro-bind", str(t.execution_snapshot_path), str(SANDBOX_PATHS.toolchain / t.name))
        )
        if t.name == "sh":
            expected.append(("--ro-bind", str(t.execution_snapshot_path), "/bin/sh"))
    expected += [
        ("--ro-bind-data", str(f.descriptor), str(f.sandbox_path))
        for f in atomic_authority.bubblewrap_bindings
    ]
    if getattr(request, "agent_source_mount", None) is not None:
        # A sealed --ro-bind-data is still an overlay: it must not shadow
        # any path below (or enclosing) the signed Agent source. Reuse the
        # original mount-descriptor rule rather than inventing another rule.
        from oh_goon.core.ports.original_mount_intent import require_unshadowed_agent_mount_targets

        try:
            require_unshadowed_agent_mount_targets(
                tuple(target for _, _, target in expected),
                request.agent_source_mount.target,
            )
        except ValueError as exc:
            raise FleetPlanInvalid(str(exc)) from exc
    # Fail closed for a changed source, target, mount mode, omitted mount, extra
    # bind or substituted sealed FD. Only the original builder constructs argv.
    # Fail-closed on a new mount-producing Bubblewrap switch: future mounts
    # must first join the original prepared-resource inventory.
    forbidden = {
        "--bind-try",
        "--ro-bind-try",
        "--dev-bind",
        "--dev-bind-try",
        "--tmpfs",
        "--overlay",
        "--ro-overlay",
        "--file",
        "--dir",
        "--symlink",
    }
    if any(flag in forbidden for flag in args):
        raise FleetPlanInvalid("Bubblewrap unregistered native mount switch")
    observed = [
        tuple(args[i : i + 3])
        for i, value in enumerate(args)
        if value in ("--bind", "--ro-bind", "--ro-bind-data")
    ]
    if observed != expected or len(set(observed)) != len(observed):
        raise FleetPlanInvalid(
            "Bubblewrap native mount argv differs from original prepared resources"
        )
    # The sealed executable intentionally overlays its frozen tool path. Exact
    # ordered comparison above admits that prepared overlay while rejecting an
    # extra mount or a reordered path bind that would hide the sealed bytes.
    for option, target in (("--proc", str(SANDBOX_PATHS.proc)), ("--dev", str(SANDBOX_PATHS.dev))):
        if sum(args[i : i + 2] == [option, target] for i in range(len(args) - 1)) != 1:
            raise FleetPlanInvalid("Bubblewrap synthetic native mount differs from prepared target")
    for f in atomic_authority.bubblewrap_bindings:
        item = ["--ro-bind-data", str(f.descriptor), str(f.sandbox_path)]
        at = next((i for i in range(len(args) - 2) if args[i : i + 3] == item), -1)
        if at < 2 or args[at - 2 : at] != ["--perms", f"{f.mode:o}"]:
            raise FleetPlanInvalid(
                "Bubblewrap sealed runtime FD or mode differs from original authority"
            )
    # The root and writable/atomic targets are still NOT signed as a complete
    # independent native resource envelope; never promote this to Gate B.


def _append_atomic_execution_mounts(
    args: list[str], atomic_authority: AtomicRuntimeAuthorityLease
) -> None:
    # Bubblewrap closes file descriptors that are not referenced by an FD-aware
    # option during its setup.  Passing /proc/self/fd/<n> as an ordinary
    # --ro-bind source therefore races against Bubblewrap's own FD cleanup and
    # fails before the mount is created.  --ro-bind-data declares the inherited
    # sealed memfd as an input, so Bubblewrap retains it and copies those exact
    # immutable bytes into a read-only bind-mounted file at the admitted path.
    for runtime_file in atomic_authority.bubblewrap_bindings:
        args.extend(
            (
                "--perms",
                f"{runtime_file.mode:o}",
                "--ro-bind-data",
                str(runtime_file.descriptor),
                str(runtime_file.sandbox_path),
            )
        )


def _append_managed_inputs(args: list[str], request: IsolatedProcessRequest) -> None:
    for binding in request.attempt.sandbox_bindings:
        verify_snapshot(
            binding.snapshot_path,
            binding.identity,
            label=f"managed runtime input {binding.declared_path}",
        )
        if binding.sandbox_path is None:
            raise FleetPlanInvalid("Managed runtime input is missing its sandbox path")
        args.extend(("--ro-bind", str(binding.snapshot_path), str(binding.sandbox_path)))


def _append_toolchain(args: list[str], bindings: tuple[HostToolBinding, ...]) -> None:
    for binding in bindings:
        verify_snapshot(
            binding.snapshot_path,
            binding.identity,
            label=f"frozen host tool {binding.name}",
        )
        verify_snapshot(
            binding.execution_snapshot_path,
            binding.execution_identity,
            label=f"frozen host tool launcher {binding.name}",
        )
        args.extend(
            (
                "--ro-bind",
                str(binding.execution_snapshot_path),
                str(SANDBOX_PATHS.toolchain / binding.name),
            )
        )
        if binding.name == "sh":
            # GNU Make may execute shell recipe fragments through /bin/sh. Expose the
            # frozen shell at that conventional interpreter path only for Steps that
            # explicitly own the sh capability; the run-wide runtime root contains
            # only a non-executable mount placeholder.
            args.extend(("--ro-bind", str(binding.execution_snapshot_path), "/bin/sh"))


def _bubblewrap_prefix(
    executable: str,
    *,
    egress_mode: str,
    require_cgroup_namespace: bool = False,
) -> list[str]:
    args = [
        executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup" if require_cgroup_namespace else "--unshare-cgroup-try",
    ]
    if egress_mode == "none":
        args.append("--unshare-net")
    elif egress_mode not in {"host_unrestricted", "cidr_allowlist"}:
        raise FleetPlanInvalid(f"Unsupported Fleet egress mode: {egress_mode}")
    return args


def _executable_sha256(path: str | None) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
