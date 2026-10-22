from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import replace
from itertools import islice
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    PlanIsolationBackendEvidence,
    PlanResourceEvidence,
    PlanStandardIsolationEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.path_authority import HostSandboxPathMapping, map_host_text_to_sandbox
from oh_goon.core.path_layouts import SANDBOX_PATHS, AttemptPathLayout
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandOutputObserver,
    CommandProcessObserver,
    CommandResult,
    CommandTimeoutExpired,
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
    standard_resource_scope_command,
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
    StandardIsolatedProcessRequest,
    StandardReadOnlyMount,
    verify_selected_backend,
)
from oh_goon.integrations.source_snapshot import verify_clean_source_snapshot

_STANDARD_RUNTIME_DIRECTORIES = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/alternatives")


class BubblewrapProcessIsolation:
    """One Bubblewrap owner for ordinary namespaces and selected strict custody."""

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
        standard_resource_executable: str | None = None,
    ) -> None:
        self.executable = executable
        self.require_local_resource_intent = require_local_resource_intent
        self.standard_resource_executable = standard_resource_executable
        self._standard_resource_environment = {
            name: os.environ[name]
            for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
            if name in os.environ
        }
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
        self._standard_available_configuration: str | None = None
        self._standard_system_checked = False
        self._application_state_paths: tuple[tuple[Path, ...], tuple[Path, ...]] | None = None

    def bind_application_state(
        self, *, private_paths: tuple[Path, ...], read_only_paths: tuple[Path, ...]
    ) -> None:
        """Receive the original application's resolved state before recovery or execution.

        The database parent can also contain independent repositories. Bind exact
        private artifacts and host tool stores, rather than excluding that parent.
        """
        if any(not path.is_absolute() for path in (*private_paths, *read_only_paths)):
            raise FleetPlanInvalid("Native application state requires resolved absolute paths")
        binding = (
            tuple(dict.fromkeys(path.resolve() for path in private_paths)),
            tuple(dict.fromkeys(path.resolve() for path in read_only_paths)),
        )
        if self._application_state_paths not in (None, binding):
            raise FleetPlanInvalid(
                "Native isolation is already bound to different application state"
            )
        self._application_state_paths = binding

    def _private_application_file_identities(self) -> frozenset[tuple[int, int]]:
        """Stat only the exact owner-bound artifacts, including newly created SQLite sidecars."""
        identities: set[tuple[int, int]] = set()
        if self._application_state_paths is not None:
            for path in self._application_state_paths[0]:
                try:
                    metadata = path.stat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise FleetPlanInvalid(
                        "Native isolation cannot establish private application file identity"
                    ) from exc
                if stat.S_ISREG(metadata.st_mode):
                    identities.add((metadata.st_dev, metadata.st_ino))
        return frozenset(identities)

    def _require_application_state_separation(
        self, request: StandardIsolatedProcessRequest
    ) -> None:
        if self._application_state_paths is None:
            return
        private_paths, read_only_paths = self._application_state_paths
        sources = (
            request.workspace,
            *(mount.source for mount in request.read_only_mounts),
            *(Path(root).resolve() for root in _STANDARD_RUNTIME_DIRECTORIES),
            *(
                (request.private_configuration_directory,)
                if request.private_configuration_directory is not None
                else ()
            ),
        )
        if any(
            source.is_relative_to(private) or private.is_relative_to(source)
            for source in sources
            for private in private_paths
        ):
            raise FleetPlanInvalid("Native mounts cannot expose private application state")
        if not request.read_only_workspace and any(
            request.workspace.is_relative_to(path) or path.is_relative_to(request.workspace)
            for path in read_only_paths
        ):
            raise FleetPlanInvalid("Native writable workspace overlaps host-managed tool authority")

    def require_standard_available(self, request: StandardIsolatedProcessRequest) -> None:
        """Prove the requested ordinary namespace capability before workload effects."""
        private_file_identities = self._validate_standard_request(request)
        if not self._standard_system_checked:
            roots: list[Path] = []
            for value in _STANDARD_RUNTIME_DIRECTORIES:
                path = Path(value).resolve()
                if path.is_dir() and not any(path.is_relative_to(root) for root in roots):
                    roots.append(path)
            system_runtime_roots = tuple(roots)
            for path in system_runtime_roots:
                _require_standard_bind_structure(
                    path,
                    system_runtime_roots=system_runtime_roots,
                    private_file_identities=private_file_identities,
                )
            self._standard_system_checked = True
        configuration = stable_id(
            "standard-bubblewrap-capability",
            {
                "backend": self.configuration_identity(),
                "resources": request.resources.model_dump(),
                "private_configuration_directory": str(request.private_configuration_directory)
                if request.private_configuration_directory is not None
                else None,
            },
        )
        if self._standard_available_configuration == configuration:
            return
        try:
            result = run_command(
                *standard_resource_scope_command(
                    request.resources, self.standard_resource_executable
                ),
                self.executable,
                "--unshare-user",
                "--unshare-net",
                "--unshare-pid",
                "--die-with-parent",
                *_standard_system_binds(),
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                *(
                    (
                        "--overlay-src",
                        str(request.private_configuration_directory),
                        "--tmp-overlay",
                        "/config/native",
                    )
                    if request.private_configuration_directory is not None
                    else ()
                ),
                "--",
                "/bin/true",
                timeout=10.0,
                env={"PATH": os.defpath, **self._standard_resource_environment},
                preserve_native_executable_path=False,
                max_capture_bytes_per_stream=4096,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FleetPlanInvalid(
                "Selected Bubblewrap namespace capability could not be established"
            ) from exc
        if result.returncode != 0:
            raise FleetPlanInvalid(
                "Selected Bubblewrap user/PID/network namespaces are unavailable: "
                + result.stderr.strip()[:1000]
            )
        self._standard_available_configuration = configuration

    def _validate_standard_request(
        self, request: StandardIsolatedProcessRequest
    ) -> frozenset[tuple[int, int]]:
        if os.name != "posix" or not Path(self.executable).is_file():
            raise FleetPlanInvalid("Standard isolated execution requires native Bubblewrap")
        if request.backend_configuration_id != self.configuration_identity():
            raise FleetPlanInvalid("Standard Bubblewrap configuration changed after resolution")
        if self.require_local_resource_intent:
            raise FleetPlanInvalid("Strict custody configuration cannot consume ordinary authority")
        self._require_application_state_separation(request)
        private_file_identities = self._private_application_file_identities()
        unsafe_environment = sorted(
            name
            for name in request.env
            if name.upper().startswith(("LD_", "DYLD_"))
            or name.upper()
            in {
                "PYTHONPATH",
                "PYTHONHOME",
                "PYTHONSTARTUP",
                "PYTHONINSPECT",
                "BASH_ENV",
                "ENV",
                "NODE_OPTIONS",
                "PERL5OPT",
                "RUBYOPT",
                "GLIBC_TUNABLES",
            }
        )
        if unsafe_environment:
            raise FleetPlanInvalid(
                "Standard namespace launcher cannot accept host loader/interpreter injection: "
                + ", ".join(unsafe_environment)
            )
        standard_resource_scope_command(request.resources, self.standard_resource_executable)
        request.require_workspace_current()
        if not request.cwd.is_dir() or not request.cwd.is_relative_to(request.workspace):
            raise FleetPlanInvalid("Standard sandbox workspace or cwd is no longer current")
        private_roots = (
            PurePosixPath("/workspace"),
            PurePosixPath(request.workspace),
            PurePosixPath("/proc"),
            PurePosixPath("/dev"),
            PurePosixPath("/home/fleet"),
            PurePosixPath("/cache"),
            PurePosixPath("/run/oh-goon-broker"),
        )
        home = Path.home().resolve()
        if home.is_relative_to(request.workspace):
            raise FleetPlanInvalid(
                "Standard workspace cannot expose the host home or its ancestors"
            )
        _require_standard_bind_structure(
            request.workspace, private_file_identities=private_file_identities
        )
        targets: list[PurePosixPath] = []
        sources: set[Path] = {request.workspace}
        selected_mounts = request.read_only_mounts + (
            (
                StandardReadOnlyMount(
                    request.private_configuration_directory, PurePosixPath("/config/native")
                ),
            )
            if request.private_configuration_directory is not None
            else ()
        )
        canonical_sources = {
            mount.source for mount in selected_mounts if mount.target == PurePosixPath(mount.source)
        }
        for mount in selected_mounts:
            source, target = mount.source, mount.target
            if (
                not source.exists()
                or source == request.workspace
                or (source in sources and source not in canonical_sources)
                or source.is_socket()
                or source == home
                or source in (Path("/tmp"), Path("/var/tmp"))
                or home.is_relative_to(source)
                or any(
                    source.is_relative_to(home / private)
                    for private in (".ssh", ".aws", ".gnupg", ".kube")
                )
                or any(
                    target == root or root.is_relative_to(target) or target.is_relative_to(root)
                    for root in private_roots
                )
                or target
                in (
                    PurePosixPath("/home"),
                    PurePosixPath("/config"),
                    PurePosixPath("/etc"),
                    PurePosixPath("/tmp"),
                )
                or any(
                    target == other or target.is_relative_to(other) or other.is_relative_to(target)
                    for other in targets
                )
            ):
                raise FleetPlanInvalid(
                    "Standard sandbox read-only mount exceeds its explicit scope"
                )
            targets.append(target)
            if source not in sources:
                _require_standard_bind_structure(
                    source, private_file_identities=private_file_identities
                )
            sources.add(source)
        if request.broker_directory is not None:
            directory = request.broker_directory
            metadata = directory.lstat()
            with os.scandir(directory) as children:
                entries = tuple(Path(entry.path) for entry in islice(children, 2))
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_uid != os.getuid()
                or len(entries) != 1
                or not stat.S_ISSOCK(entries[0].lstat().st_mode)
            ):
                raise FleetPlanInvalid(
                    "Standard model broker requires one socket in a private directory"
                )
        return private_file_identities

    def _execute_standard(
        self,
        request: StandardIsolatedProcessRequest,
        *,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None,
        process_observer: CommandProcessObserver | None,
        runtime_supervision: CommandRuntimeSupervision | None,
    ) -> CommandResult:
        if cancel_requested is not None and cancel_requested():
            from oh_goon.engine.errors import OperationCancelledError

            raise OperationCancelledError("Standard sandbox cancelled before preparation")
        self.require_standard_available(request)
        from oh_goon.integrations.fleet_plan_run import _write_minimal_etc

        with (
            tempfile.TemporaryDirectory(prefix="oh-goon-isolated-") as directory,
            tempfile.TemporaryFile() as status,
        ):
            paths = AttemptPathLayout.from_root(Path(directory))
            for path in (paths.home, paths.tmp, paths.cache, paths.config, paths.etc):
                path.mkdir()
            _write_minimal_etc(paths.etc)
            args = _bubblewrap_prefix(self.executable, egress_mode="none")
            args.insert(1, "--unshare-user")
            args.extend(("--json-status-fd", str(status.fileno())))
            if request.terminal is not None:
                args.remove("--new-session")
            for name in self._standard_resource_environment:
                args.extend(("--unsetenv", name))
            args.extend(_standard_system_binds())
            args.extend(("--proc", "/proc", "--dev", "/dev"))
            for name in ("passwd", "group", "nsswitch.conf", "hosts"):
                args.extend(("--ro-bind", str(paths.etc / name), "/etc/" + name))
            workspace_mode = "--ro-bind" if request.read_only_workspace else "--bind"
            args.extend((workspace_mode, str(request.workspace), "/workspace"))
            for source, target in (
                (paths.home, "/home/fleet"),
                (paths.tmp, "/tmp"),
                (paths.cache, "/cache"),
                (paths.config, "/config"),
            ):
                args.extend(("--bind", str(source), target))
            if str(request.workspace) != "/workspace":
                args.extend((workspace_mode, str(request.workspace), str(request.workspace)))
            for mount in request.read_only_mounts:
                args.extend(("--ro-bind", str(mount.source), str(mount.target)))
            if request.private_configuration_directory is not None:
                args.extend(
                    (
                        "--overlay-src",
                        str(request.private_configuration_directory),
                        "--tmp-overlay",
                        "/config/native",
                    )
                )
            if request.broker_directory is not None:
                args.extend(("--ro-bind", str(request.broker_directory), "/run/oh-goon-broker"))
            mapping = tuple(
                sorted(
                    [
                        HostSandboxPathMapping.create(request.workspace, SANDBOX_PATHS.workspace),
                        *(
                            HostSandboxPathMapping.create(mount.source, mount.target)
                            for mount in request.read_only_mounts
                            if mount.target == PurePosixPath(mount.source)
                            or not any(
                                other.source == mount.source
                                and other.target == PurePosixPath(other.source)
                                for other in request.read_only_mounts
                            )
                        ),
                        *(
                            (
                                HostSandboxPathMapping.create(
                                    request.private_configuration_directory,
                                    PurePosixPath("/config/native"),
                                ),
                            )
                            if request.private_configuration_directory is not None
                            else ()
                        ),
                    ],
                    key=lambda item: len(str(item.host_root)),
                    reverse=True,
                )
            )
            argv = tuple(map_host_text_to_sandbox(value, mapping) for value in request.argv)
            environment = {
                name: (
                    os.pathsep.join(
                        map_host_text_to_sandbox(part, mapping) for part in value.split(os.pathsep)
                    )
                    if name == "PATH"
                    else map_host_text_to_sandbox(value, mapping)
                )
                for name, value in request.env.items()
                if name not in {"SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"}
            }
            environment.update(
                {
                    "HOME": "/home/fleet",
                    "USERPROFILE": "/home/fleet",
                    "TMPDIR": "/tmp",
                    "TMP": "/tmp",
                    "TEMP": "/tmp",
                    "XDG_CACHE_HOME": "/cache",
                    "XDG_CONFIG_HOME": "/config",
                    "XDG_DATA_HOME": "/home/fleet/.local/share",
                    "XDG_STATE_HOME": "/home/fleet/.local/state",
                }
            )
            args.extend(
                (
                    "--chdir",
                    str(request.cwd),
                    "--",
                    *argv,
                )
            )
            self._validate_standard_request(request)
            try:
                result = run_command(
                    *standard_resource_scope_command(
                        request.resources, self.standard_resource_executable
                    ),
                    *args,
                    timeout=timeout,
                    env={**environment, **self._standard_resource_environment},
                    preserve_native_executable_path=False,
                    cancel_requested=cancel_requested,
                    termination_requested=request.termination_requested,
                    stream_output=stream_output,
                    process_observer=process_observer,
                    runtime_supervision=runtime_supervision,
                    terminal=request.terminal,
                    input_data=request.input_data,
                    pass_fds=(status.fileno(),),
                    max_capture_bytes_per_stream=MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
                    retain_output_tail=request.retain_output_tail,
                )
            except (CommandTimeoutExpired, CommandCancelledError) as exc:
                status.seek(0)
                if _standard_workload_exec_completed(status.read(4097), None):
                    exc.standard_isolation = _standard_isolation_evidence(request)
                raise
            status.seek(0)
            if not _standard_workload_exec_completed(status.read(4097), result.returncode):
                if result.returncode == 0:
                    raise FleetPlanInvalid(
                        "Standard sandbox completed without native workload-exec evidence"
                    )
                return result
            return replace(
                result,
                standard_isolation=_standard_isolation_evidence(request),
            )

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
                **(
                    {
                        "standard_resource_executable": _executable_sha256(
                            self.standard_resource_executable
                        )
                    }
                    if self.standard_resource_executable is not None
                    else {}
                ),
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
        request: IsolatedProcessRequest | StandardIsolatedProcessRequest,
        *,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
    ) -> CommandResult:
        if isinstance(request, StandardIsolatedProcessRequest):
            return self._execute_standard(
                request,
                timeout=timeout,
                cancel_requested=cancel_requested,
                stream_output=stream_output,
                process_observer=process_observer,
                runtime_supervision=runtime_supervision,
            )
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
                        preserve_native_executable_path=False,
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


def _standard_isolation_evidence(
    request: StandardIsolatedProcessRequest,
) -> PlanStandardIsolationEvidence:
    policy = request.resources
    limited = any(value is not None for value in policy.model_dump().values())
    return PlanStandardIsolationEvidence(
        backend=PlanIsolationBackendEvidence(
            backend_id="bubblewrap",
            configuration_id=request.backend_configuration_id,
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
        workspace_read_only=request.read_only_workspace,
        broker_socket_mounted=request.broker_directory is not None,
        private_configuration_overlay=request.private_configuration_directory is not None,
        resources=PlanResourceEvidence(
            cpu_millis=policy.cpu_millis,
            memory_bytes=policy.memory_bytes,
            max_processes=policy.max_processes,
        )
        if limited
        else None,
        resource_owner="systemd_user_scope" if limited else "none",
    )


def _standard_workload_exec_completed(payload: bytes, returncode: int | None) -> bool:
    """Use Bubblewrap's native successful-exec acknowledgement, not exit status alone.

    The native monitor closes this descriptor in the sandbox child. It emits
    exit-code only after setup and exec succeeded, including nonzero workloads.
    Namespace/setup/exec failures have no final exit-code acknowledgement.
    """
    if len(payload) > 4096:
        return False
    try:
        records = [json.loads(line) for line in payload.splitlines() if line]
    except (UnicodeDecodeError, ValueError):
        return False
    return (
        len(records) == 2
        and isinstance(records[0], dict)
        and type(records[0].get("child-pid")) is int
        and records[0]["child-pid"] > 0
        and isinstance(records[1], dict)
        and set(records[1]) == {"exit-code"}
        and type(records[1]["exit-code"]) is int
        and (returncode is None or records[1]["exit-code"] == returncode)
    )


def _standard_system_binds() -> tuple[str, ...]:
    """Read-only operating-system runtime paths, excluding host homes and services."""
    arguments: list[str] = []
    for value in _STANDARD_RUNTIME_DIRECTORIES:
        path = Path(value)
        if path.is_dir():
            arguments.extend(("--ro-bind", str(path.resolve()), value))
    return tuple(arguments)


def _require_standard_bind_structure(
    root: Path,
    *,
    system_runtime_roots: tuple[Path, ...] = (),
    private_file_identities: frozenset[tuple[int, int]] = frozenset(),
) -> None:
    """Reject host IPC/special objects without reading file contents or following directory links.

    This is a bounded physical mount check, not repository retrieval or another
    source identity. System runtime links to synthetic /dev devices resolve
    inside the sandbox. Host IPC outside the bound system roots is absent from
    that view; the workspace and selected inputs independently reject host IPC.
    """
    pending = [root]
    inspected = 0
    synthetic = {Path("/dev") / name for name in ("null", "zero", "random", "urandom", "tty")}
    while pending:
        path = pending.pop()
        metadata = path.lstat()
        inspected += 1
        if inspected > 1_000_000:
            raise FleetPlanInvalid("Standard sandbox bind exceeds its physical entry budget")
        if (metadata.st_dev, metadata.st_ino) in private_file_identities:
            raise FleetPlanInvalid("Native bind exposes a private application file alias")
        if stat.S_ISLNK(metadata.st_mode):
            try:
                target = path.resolve(strict=True)
                target_metadata = target.stat()
                target_mode = target_metadata.st_mode
            except FileNotFoundError:
                continue  # A dangling link conveys no host endpoint.
            if (target_metadata.st_dev, target_metadata.st_ino) in private_file_identities:
                raise FleetPlanInvalid("Native bind exposes a private application file alias")
            if system_runtime_roots and (
                target in synthetic
                or (
                    (stat.S_ISSOCK(target_mode) or stat.S_ISFIFO(target_mode))
                    and not any(target.is_relative_to(bound) for bound in system_runtime_roots)
                )
            ):
                continue
            if not (stat.S_ISREG(target_mode) or stat.S_ISDIR(target_mode)):
                raise FleetPlanInvalid(
                    "Standard sandbox bind symlink exposes a host IPC/special object: "
                    f"{path} -> {target}"
                )
        elif stat.S_ISDIR(metadata.st_mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    inspected += 1
                    if inspected > 1_000_000:
                        raise FleetPlanInvalid(
                            "Standard sandbox bind exceeds its physical entry budget"
                        )
                    entry_metadata = entry.stat(follow_symlinks=False)
                    mode = entry_metadata.st_mode
                    if (entry_metadata.st_dev, entry_metadata.st_ino) in private_file_identities:
                        raise FleetPlanInvalid(
                            "Native bind exposes a private application file alias"
                        )
                    if stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                        if len(pending) >= 4096:
                            raise FleetPlanInvalid(
                                "Standard sandbox bind exceeds its pending-directory budget"
                            )
                        pending.append(Path(entry.path))
                    elif not stat.S_ISREG(mode):
                        raise FleetPlanInvalid(
                            "Standard sandbox bind exposes a host IPC/special object"
                        )
        elif not stat.S_ISREG(metadata.st_mode):
            raise FleetPlanInvalid("Standard sandbox bind exposes a host IPC/special object")
