from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.file_digest import read_prefix
from oh_goon.core.fleet_plan_tool_requirements import required_runtime_tool_names
from oh_goon.core.models.fleet_plan import (
    ResolvedFleetPlan,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
    ResolvedOciPublishStep,
    ResolvedOciTagAssertStep,
)
from oh_goon.core.models.fleet_plan_kubernetes import (
    validate_kubernetes_bearer_token,
    validate_kubernetes_ca_certificate,
)
from oh_goon.core.path_authority import StrictHostPathAuthority, canonical_path
from oh_goon.integrations.content_snapshot import (
    SnapshotIdentity,
    snapshot_host_path,
    snapshot_identity,
)
from oh_goon.integrations.content_snapshot_dereferenced import snapshot_directory_dereferenced
from oh_goon.integrations.fleet_plan_environment import step_host_environment_names


@dataclass(frozen=True, slots=True)
class HostToolRuntimeSupport:
    """Frozen support tree required by one non-standalone host tool launcher."""

    snapshot_path: Path
    identity: SnapshotIdentity
    sandbox_path: PurePosixPath
    entry_relative: PurePosixPath


@dataclass(frozen=True, slots=True)
class HostToolBinding:
    """One declared host tool copied into the immutable run toolchain.

    ``identity`` always describes the admitted host executable bytes. Script tools may
    use a Fleet-generated launcher plus a frozen support tree at execution time; those
    artifacts are separately identity-bound so evidence never mistakes the launcher for
    the host tool itself.
    """

    name: str
    source_path: Path
    snapshot_path: Path
    identity: SnapshotIdentity
    launch_snapshot_path: Path | None = None
    launch_identity: SnapshotIdentity | None = None
    runtime_support: HostToolRuntimeSupport | None = None
    execution_sha256: str = ""

    @property
    def execution_snapshot_path(self) -> Path:
        return self.launch_snapshot_path or self.snapshot_path

    @property
    def execution_identity(self) -> SnapshotIdentity:
        return self.launch_identity or self.identity


def _validate_host_environment(
    plan: ResolvedFleetPlan, environment: Mapping[str, str]
) -> tuple[str, ...]:
    """Validate declared host imports without retaining secret bytes as evidence."""

    names = tuple(
        sorted({name for step in plan.steps for name in step_host_environment_names(step)})
    )
    missing = [name for name in names if name not in environment]
    if missing:
        raise FleetPlanInvalid(
            "Fleet plan requires missing imported host environment values: " + ", ".join(missing)
        )
    credential_names = {
        name
        for step in plan.steps
        if isinstance(step, (ResolvedOciPublishStep, ResolvedOciTagAssertStep))
        for name in step.credentials.host_environment_names
    }
    empty_credentials = sorted(name for name in credential_names if environment[name] == "")
    if empty_credentials:
        raise FleetPlanInvalid(
            "Fleet OCI registry operations require non-empty host credential values: "
            + ", ".join(empty_credentials)
        )
    for step in plan.steps:
        if not isinstance(
            step, (ResolvedKubernetesSetImageStep, ResolvedKubernetesRolloutVerifyStep)
        ):
            continue
        try:
            validate_kubernetes_bearer_token(environment[step.credentials.token_host_env])
            validate_kubernetes_ca_certificate(
                environment[step.credentials.ca_certificate_host_env]
            )
        except ValueError as exc:
            raise FleetPlanInvalid(
                f"Fleet Kubernetes step {step.step_id} has invalid host credential material: {exc}"
            ) from exc
    return names


def capture_host_environment(plan: ResolvedFleetPlan | None) -> dict[str, str]:
    if plan is None:
        return {}
    names = _validate_host_environment(plan, os.environ)
    return {name: os.environ[name] for name in names}


def preflight_host_environment_feasibility(
    active_plans: tuple[ResolvedFleetPlan, ...],
    environment: Mapping[str, str] | None = None,
) -> None:
    """Reject deterministic host-import/credential defects before RUN_ADMITTED.

    Applicability is resolved against each selected repository so an optional imported
    Goon cannot demand credentials where it will not execute. Preparation later validates
    the same values again and only then captures them into private run authority.
    """

    source = os.environ if environment is None else environment
    for active_plan in active_plans:
        _validate_host_environment(active_plan, source)


def capture_host_tools(
    plan: ResolvedFleetPlan | None,
    toolchain_root: Path,
    path_authority: StrictHostPathAuthority,
    cancel_requested: Callable[[], bool] | None,
) -> dict[str, HostToolBinding]:
    """Freeze only the execution-environment tools the resolved run actually needs."""

    if plan is None:
        return {}
    result: dict[str, HostToolBinding] = {}
    node_support: dict[Path, HostToolRuntimeSupport] = {}
    required_names = required_runtime_tool_names(plan)
    for index, name in enumerate(required_names):
        path = canonical_path(path_authority.tool(name))
        binding = snapshot_host_path(
            path,
            toolchain_root / f"{index:03d}-{name}",
            label=f"Fleet execution-environment tool {name}",
            cancel_requested=cancel_requested,
        )
        runtime_support = _capture_node_cli_support(
            path,
            binding.identity,
            toolchain_root=toolchain_root,
            support_cache=node_support,
            cancel_requested=cancel_requested,
        )
        launch_path: Path | None = None
        launch_identity: SnapshotIdentity | None = None
        if runtime_support is not None:
            if "node" not in required_names:
                raise FleetPlanInvalid(
                    f"Strict Fleet Node CLI {name!r} requires explicit child tool capability 'node'"
                )
            launch_path = toolchain_root / f"{index:03d}-{name}.launcher"
            launch_path.write_text(
                _node_cli_launcher(runtime_support),
                encoding="utf-8",
            )
            launch_path.chmod(0o755)
            launch_identity = snapshot_identity(launch_path)
        execution_snapshot_path = launch_path or binding.snapshot_path
        result[name] = HostToolBinding(
            name=name,
            source_path=path,
            snapshot_path=binding.snapshot_path,
            identity=binding.identity,
            launch_snapshot_path=launch_path,
            launch_identity=launch_identity,
            runtime_support=runtime_support,
            execution_sha256=_raw_sha256(execution_snapshot_path),
        )
    return result


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _capture_node_cli_support(
    source: Path,
    source_identity: SnapshotIdentity,
    *,
    toolchain_root: Path,
    support_cache: dict[Path, HostToolRuntimeSupport],
    cancel_requested: Callable[[], bool] | None,
) -> HostToolRuntimeSupport | None:
    """Freeze the package tree for a Node CLI whose entrypoint uses relative modules.

    Node-distributed tools such as npm/npx are not standalone executable files: their
    ``bin/*.js`` entrypoints load sibling package modules. Copying only the entrypoint
    to ``/toolchain/bin`` therefore changes module-relative identity. The nearest
    package.json owner is the single support-tree authority for that CLI.
    """

    header = read_prefix(source, 512)
    if not _node_shebang(header):
        return None
    package_root = _nearest_node_package_root(source)
    if package_root is None:
        raise FleetPlanInvalid(f"Strict Fleet Node CLI has no package.json runtime owner: {source}")
    entry_relative = PurePosixPath(*source.relative_to(package_root).parts)
    cached = support_cache.get(package_root)
    if cached is None:
        support_index = len(support_cache)
        support_snapshot = toolchain_root / "support" / f"{support_index:03d}"
        identity = snapshot_directory_dereferenced(
            package_root,
            support_snapshot,
            label=f"Fleet Node CLI runtime support {package_root.name}",
            cancel_requested=cancel_requested,
        )
        cached = HostToolRuntimeSupport(
            snapshot_path=support_snapshot.resolve(),
            identity=identity,
            sandbox_path=PurePosixPath("/toolchain/support") / f"{support_index:03d}",
            entry_relative=entry_relative,
        )
        support_cache[package_root] = cached
    else:
        cached = HostToolRuntimeSupport(
            snapshot_path=cached.snapshot_path,
            identity=cached.identity,
            sandbox_path=cached.sandbox_path,
            entry_relative=entry_relative,
        )
    frozen_entry = cached.snapshot_path / Path(*entry_relative.parts)
    if snapshot_identity(frozen_entry) != source_identity:
        raise FleetPlanInvalid(
            f"Fleet Node CLI entrypoint changed while its runtime support was frozen: {source}"
        )
    return cached


def _node_shebang(header: bytes) -> bool:
    if not header.startswith(b"#!"):
        return False
    try:
        line = header.splitlines()[0][2:].decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return False
    parts = line.split()
    if not parts:
        return False
    interpreter = Path(parts[0]).name
    if interpreter == "node":
        return True
    return interpreter == "env" and len(parts) >= 2 and parts[1] == "node"


def _nearest_node_package_root(source: Path) -> Path | None:
    for parent in source.parents:
        if (parent / "package.json").is_file():
            return parent.resolve()
    return None


def _node_cli_launcher(support: HostToolRuntimeSupport) -> str:
    entry = str(support.sandbox_path / support.entry_relative)
    return "#!/toolchain/bin/node\nrequire(" + json.dumps(entry) + ")\n"


def _preflight_tool_names(names: tuple[str, ...], path_authority: StrictHostPathAuthority) -> None:
    missing: list[str] = []
    for name in names:
        try:
            path_authority.tool(name)
        except FleetPlanInvalid:
            missing.append(name)
    if missing:
        raise FleetPlanInvalid(
            "Strict Fleet execution environment is missing required tool(s): " + ", ".join(missing)
        )


def preflight_executor_tool_feasibility(
    active_plans: tuple[ResolvedFleetPlan, ...],
    path_authority: StrictHostPathAuthority,
) -> None:
    """Reject deterministic executor-tool impossibility before RUN_ADMITTED.

    Imported Steps contribute tools only when at least one selected repository makes the
    import applicable. Preparation revalidates the same authority after admission so this
    cheap feasibility proof never becomes stale execution evidence.
    """

    required: set[str] = set()
    for active_plan in active_plans:
        required.update(required_runtime_tool_names(active_plan))
    _preflight_tool_names(tuple(sorted(required)), path_authority)


def preflight_run_capabilities(
    plan: ResolvedFleetPlan, path_authority: StrictHostPathAuthority
) -> None:
    """Authoritatively revalidate every required tool at strict-run preparation."""

    _preflight_tool_names(required_runtime_tool_names(plan), path_authority)
