from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedPlanStep
from oh_goon.integrations._shell_authority_syntax import materialized_executable_identity
from oh_goon.integrations.content_snapshot import SnapshotIdentity, snapshot_identity
from oh_goon.integrations.make_execution_authority import derive_make_target_authority
from oh_goon.integrations.package_script_execution_authority import (
    PackageExecutableAuthority,
    derive_npm_run_authority,
    npm_run_script,
)
from oh_goon.integrations.shell_execution_authority import derive_shell_fragment_authority
from oh_goon.integrations.workload_execution_requirements import make_target_owner

_SHELL_EXECUTABLES = frozenset({"bash", "dash", "sh"})


@dataclass(frozen=True, slots=True)
class LogicalStepExecutionAuthority:
    """Static project-owned sources and package executables admitted before execution."""

    source_identities: tuple[tuple[str, str], ...] = ()
    package_executables: tuple[PackageExecutableAuthority, ...] = ()
    materialized_executables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FileMaterializationReceipt:
    relative_path: str
    sha256: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class PackageExecutableMaterializationReceipt:
    authority: PackageExecutableAuthority
    bin_entry: str
    link_target: str
    resolved_target: str
    wrapper_sha256: str
    target_identity: SnapshotIdentity
    provider_identity: SnapshotIdentity

    @property
    def receipt_identity(self) -> str:
        payload = {
            "authority": list(self.authority.identity_row()),
            "package_directory": self.authority.package_directory,
            "bin_entry": self.bin_entry,
            "link_target": self.link_target,
            "resolved_target": self.resolved_target,
            "wrapper_sha256": self.wrapper_sha256,
            "target_identity": _snapshot_payload(self.target_identity),
            "provider_identity": _snapshot_payload(self.provider_identity),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class StepExecutableMaterializationReceipt:
    """Exact executable/source materialization that may cross the launch cut."""

    logical_sources: tuple[FileMaterializationReceipt, ...] = ()
    package_executables: tuple[PackageExecutableMaterializationReceipt, ...] = ()
    owned_scripts: tuple[FileMaterializationReceipt, ...] = ()
    direct_workspace_executable: FileMaterializationReceipt | None = None
    economics: ExecutionAuthorityEconomicsReceipt | None = None

    @property
    def receipt_identity(self) -> str:
        payload = {
            "logical_sources": [_file_receipt_payload(item) for item in self.logical_sources],
            "package_executables": [item.receipt_identity for item in self.package_executables],
            "owned_scripts": [_file_receipt_payload(item) for item in self.owned_scripts],
            "direct_workspace_executable": (
                _file_receipt_payload(self.direct_workspace_executable)
                if self.direct_workspace_executable is not None
                else None
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def logical_step_execution_authority(
    root: Path, step: ResolvedPlanStep
) -> LogicalStepExecutionAuthority:
    """Bind static execution sources/providers while the admitted source is immutable."""

    if not isinstance(step, ResolvedCommandStep):
        return LogicalStepExecutionAuthority()
    owner = make_target_owner(step.argv)
    if owner is not None:
        authority = derive_make_target_authority(root, owner.removeprefix("make:"))
        _require_resolved(step, authority.unresolved, owner)
        return LogicalStepExecutionAuthority(
            source_identities=authority.source_identities,
            package_executables=authority.package_executables,
            materialized_executables=authority.materialized_executables,
        )
    if step.argv and step.argv[0] == "npm":
        script = npm_run_script(step.argv, 0)
        if script is None:
            return LogicalStepExecutionAuthority()
        package_dir = _step_working_directory(root, step)
        authority = derive_npm_run_authority(
            root,
            script,
            chain=(f"step:{step.step_id}", f"npm run {script}"),
            package_dir=package_dir,
        )
        _require_resolved(step, authority.unresolved, f"npm run {script}")
        return LogicalStepExecutionAuthority(
            source_identities=tuple(
                (relative, _sha256_file(root / relative)[0]) for relative in authority.sources
            ),
            package_executables=authority.package_executables,
        )
    if step.argv and step.argv[0] in _SHELL_EXECUTABLES:
        authority = derive_shell_fragment_authority(
            root,
            shlex.join(step.argv),
            chain=(f"step:{step.step_id}",),
            working_directory=step.working_directory,
        )
        _require_resolved(step, authority.unresolved, "explicit shell command")
        return LogicalStepExecutionAuthority(
            materialized_executables=authority.materialized_executables,
        )
    materialized = (
        materialized_executable_identity(
            step.argv[0],
            working_directory=step.working_directory,
        )
        if step.argv
        else None
    )
    if materialized is not None:
        return LogicalStepExecutionAuthority(materialized_executables=(materialized,))
    return LogicalStepExecutionAuthority()


def bind_step_executable_materialization(
    workspace: Path,
    step: ResolvedPlanStep,
    logical: LogicalStepExecutionAuthority,
    *,
    allowed_host_tools: tuple[str, ...],
) -> StepExecutableMaterializationReceipt:
    """Prove physical execution bytes after the exact attempt workspace is copied."""

    workspace = workspace.resolve()
    logical_sources = tuple(
        _bind_expected_source(workspace, relative, expected)
        for relative, expected in logical.source_identities
    )
    dynamic_packages: tuple[PackageExecutableAuthority, ...] = ()
    owned_scripts: tuple[FileMaterializationReceipt, ...] = ()
    direct_executable: FileMaterializationReceipt | None = None

    if isinstance(step, ResolvedCommandStep):
        dynamic_packages, owned_scripts = _bind_explicit_shell_authority(
            workspace,
            step,
            allowed_host_tools=allowed_host_tools,
        )
        _require_pre_admitted_packages(step, logical.package_executables, dynamic_packages)
        direct_executable = _bind_direct_workspace_executable(workspace, step)

    packages = tuple(sorted(set(logical.package_executables) | set(dynamic_packages)))
    package_receipts = tuple(
        _bind_package_executable(workspace, authority) for authority in packages
    )
    receipt = StepExecutableMaterializationReceipt(
        logical_sources=logical_sources,
        package_executables=package_receipts,
        owned_scripts=owned_scripts,
        direct_workspace_executable=direct_executable,
    )
    return StepExecutableMaterializationReceipt(
        logical_sources=receipt.logical_sources,
        package_executables=receipt.package_executables,
        owned_scripts=receipt.owned_scripts,
        direct_workspace_executable=receipt.direct_workspace_executable,
        economics=_materialization_economics(
            receipt,
            reason="new-physical-attempt-materialization",
        ),
    )


def verify_step_executable_materialization(
    workspace: Path,
    receipt: StepExecutableMaterializationReceipt,
) -> ExecutionAuthorityEconomicsReceipt:
    """Re-prove the bounded materialization immediately before process launch."""

    workspace = workspace.resolve()
    for source in receipt.logical_sources:
        observed = _file_receipt(workspace, source.relative_path)
        if observed != source:
            raise FleetPlanInvalid(
                f"Execution-authority source changed after materialization binding: "
                f"{source.relative_path}"
            )
    for script in receipt.owned_scripts:
        observed = _file_receipt(workspace, script.relative_path)
        if observed != script:
            raise FleetPlanInvalid(
                f"Owned executable script changed after materialization binding: "
                f"{script.relative_path}"
            )
    if receipt.direct_workspace_executable is not None:
        observed = _file_receipt(workspace, receipt.direct_workspace_executable.relative_path)
        if observed != receipt.direct_workspace_executable:
            raise FleetPlanInvalid(
                "Workspace executable changed after materialization binding: "
                f"{receipt.direct_workspace_executable.relative_path}"
            )
    for package in receipt.package_executables:
        observed = _bind_package_executable(workspace, package.authority)
        if observed != package:
            raise FleetPlanInvalid(
                "Package-local executable changed after materialization binding: "
                f"{package.authority.bin_name}"
            )
    return _materialization_economics(
        receipt,
        reason="attempt-workspace-mutable-before-launch",
    )


def _materialization_economics(
    receipt: StepExecutableMaterializationReceipt,
    *,
    reason: str,
) -> ExecutionAuthorityEconomicsReceipt:
    files = (
        *receipt.logical_sources,
        *receipt.owned_scripts,
        *((receipt.direct_workspace_executable,) if receipt.direct_workspace_executable else ()),
    )
    units = len(files) + 2 * len(receipt.package_executables)
    hashed_bytes = sum(item.byte_count for item in files) + sum(
        item.target_identity.byte_count + item.provider_identity.byte_count
        for item in receipt.package_executables
    )
    return ExecutionAuthorityEconomicsReceipt(
        scope="materialization",
        candidate_units=units,
        reused_units=0,
        recomputed_units=units,
        reused_bytes=0,
        recomputed_bytes=hashed_bytes,
        recompute_reasons=(reason,) if units else (),
    )


def _bind_explicit_shell_authority(
    workspace: Path,
    step: ResolvedCommandStep,
    *,
    allowed_host_tools: tuple[str, ...],
) -> tuple[tuple[PackageExecutableAuthority, ...], tuple[FileMaterializationReceipt, ...]]:
    if not step.argv or step.argv[0] not in _SHELL_EXECUTABLES:
        return (), ()
    authority = derive_shell_fragment_authority(
        workspace,
        shlex.join(step.argv),
        chain=(f"step:{step.step_id}",),
        working_directory=step.working_directory,
    )
    _require_resolved(step, authority.unresolved, "explicit shell command")
    missing = tuple(sorted(set(authority.tools) - set(allowed_host_tools)))
    if missing:
        raise FleetPlanInvalid(
            f"Fleet plan step {step.step_id} generated/owned script requires undeclared "
            f"host executable authority: {', '.join(missing)}"
        )
    scripts = tuple(_file_receipt(workspace, relative) for relative in authority.scripts)
    return authority.package_executables, scripts


def _require_pre_admitted_packages(
    step: ResolvedCommandStep,
    admitted: tuple[PackageExecutableAuthority, ...],
    observed: tuple[PackageExecutableAuthority, ...],
) -> None:
    missing = tuple(sorted(set(observed) - set(admitted)))
    if not missing:
        return
    names = ", ".join(item.bin_name for item in missing)
    raise FleetPlanInvalid(
        f"Fleet plan step {step.step_id} generated/owned script acquired package-local "
        f"executable authority that was not admitted before execution: {names}"
    )


def _bind_direct_workspace_executable(
    workspace: Path, step: ResolvedCommandStep
) -> FileMaterializationReceipt | None:
    executable = step.argv[0]
    if "/" not in executable and "\\" not in executable:
        return None
    candidate = Path(executable)
    if not candidate.is_absolute():
        candidate = _step_working_directory(workspace, step) / candidate
    try:
        lexical = candidate.absolute()
        lexical.relative_to(workspace)
    except ValueError:
        return None
    if lexical.is_symlink():
        raise FleetPlanInvalid(
            f"Fleet plan step {step.step_id} direct workspace executable symlink is not "
            "admissible before executable-resolution TOCTOU closure"
        )
    try:
        relative = lexical.resolve(strict=True).relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return None
    return _file_receipt(workspace, relative)


def _bind_expected_source(
    workspace: Path, relative: str, expected: str
) -> FileMaterializationReceipt:
    receipt = _file_receipt(workspace, relative)
    if receipt.sha256 != expected:
        raise FleetPlanInvalid(
            f"Execution-authority source no longer matches admitted identity: {relative}"
        )
    return receipt


def _file_receipt(workspace: Path, relative: str) -> FileMaterializationReceipt:
    path = _contained_regular_file(workspace, relative, label="execution-authority source")
    digest, byte_count = _sha256_file(path)
    return FileMaterializationReceipt(relative, digest, byte_count)


def _bind_package_executable(
    workspace: Path, authority: PackageExecutableAuthority
) -> PackageExecutableMaterializationReceipt:
    package_root, node_modules, node_modules_resolved = _materialized_node_modules(
        workspace, authority
    )
    bin_entry, mode, link_target, resolved_target = _materialized_bin_link(
        workspace, node_modules, node_modules_resolved, authority
    )
    provider_root, expected_target = _materialized_provider_target(
        package_root, node_modules_resolved, authority
    )
    if resolved_target != expected_target:
        raise FleetPlanInvalid(
            f"Package-local executable {authority.bin_name} resolves to a different provider "
            f"target than admitted: {_display(workspace, resolved_target)}"
        )
    if not resolved_target.is_file():
        raise FleetPlanInvalid(
            f"Package-local executable {authority.bin_name} target is not a regular file"
        )
    _verify_provider_manifest(provider_root, authority)
    wrapper_payload = _symlink_wrapper_payload(mode, link_target)
    return PackageExecutableMaterializationReceipt(
        authority=authority,
        bin_entry=_display(workspace, bin_entry),
        link_target=link_target,
        resolved_target=_display(workspace, resolved_target),
        wrapper_sha256="sha256:" + hashlib.sha256(wrapper_payload).hexdigest(),
        target_identity=snapshot_identity(resolved_target),
        provider_identity=snapshot_identity(provider_root),
    )


def _materialized_node_modules(
    workspace: Path, authority: PackageExecutableAuthority
) -> tuple[Path, Path, Path]:
    package_directory = _safe_relative(authority.package_directory, "package directory")
    package_root = workspace.joinpath(*package_directory.parts)
    node_modules = package_root / "node_modules"
    if not node_modules.is_dir() or node_modules.is_symlink():
        raise FleetPlanInvalid(
            f"Package-local executable {authority.bin_name} has no materialized node_modules"
        )
    node_modules_resolved = node_modules.resolve(strict=True)
    _require_within(workspace, node_modules_resolved, "materialized node_modules")
    return package_root, node_modules, node_modules_resolved


def _materialized_bin_link(
    workspace: Path,
    node_modules: Path,
    node_modules_resolved: Path,
    authority: PackageExecutableAuthority,
) -> tuple[Path, int, str, Path]:
    bin_entry = node_modules / ".bin" / authority.bin_name
    try:
        mode = bin_entry.lstat().st_mode
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Package-local executable {authority.bin_name} is not materialized at "
            f"{_display(workspace, bin_entry)}"
        ) from exc
    if not stat.S_ISLNK(mode):
        raise FleetPlanInvalid(
            f"Package-local executable {authority.bin_name} must be an npm symlink in strict "
            "POSIX execution materialization"
        )
    link_target = _read_package_bin_link(bin_entry, authority.bin_name)
    if Path(link_target).is_absolute():
        raise FleetPlanInvalid(
            f"Package-local executable {authority.bin_name} has an absolute materialized target"
        )
    try:
        resolved_target = (bin_entry.parent / link_target).resolve(strict=True)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Package-local executable {authority.bin_name} has a missing materialized target"
        ) from exc
    _require_within(node_modules_resolved, resolved_target, "package-local executable target")
    return bin_entry, mode, link_target, resolved_target


def _read_package_bin_link(bin_entry: Path, bin_name: str) -> str:
    try:
        return os.readlink(bin_entry)
    except OSError as exc:
        raise FleetPlanInvalid(f"Cannot inspect package-local executable link {bin_name}") from exc


def _materialized_provider_target(
    package_root: Path,
    node_modules_resolved: Path,
    authority: PackageExecutableAuthority,
) -> tuple[Path, Path]:
    package_path = _safe_relative(authority.package_path, "locked package path")
    bin_target = _safe_relative(authority.bin_target, "package bin target")
    provider_root = package_root.joinpath(*package_path.parts)
    if provider_root.is_symlink() or not provider_root.is_dir():
        raise FleetPlanInvalid(
            f"Locked provider for package-local executable {authority.bin_name} is not a "
            "materialized directory"
        )
    provider_resolved = provider_root.resolve(strict=True)
    _require_within(node_modules_resolved, provider_resolved, "locked package provider")
    try:
        expected_target = provider_root.joinpath(*bin_target.parts).resolve(strict=True)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Locked provider for package-local executable {authority.bin_name} is missing "
            "its admitted bin target"
        ) from exc
    _require_within(provider_resolved, expected_target, "locked provider bin target")
    return provider_root, expected_target


def _symlink_wrapper_payload(mode: int, link_target: str) -> bytes:
    return json.dumps(
        {"kind": "symlink", "mode": stat.S_IMODE(mode), "target": link_target},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _verify_provider_manifest(provider_root: Path, authority: PackageExecutableAuthority) -> None:
    manifest = provider_root / "package.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise FleetPlanInvalid(
            f"Materialized provider for {authority.bin_name} has no regular package.json"
        )
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetPlanInvalid(
            f"Cannot inspect materialized provider manifest for {authority.bin_name}"
        ) from exc
    expected_name = _package_name(authority.package_path)
    if payload.get("name") != expected_name or payload.get("version") != authority.version:
        raise FleetPlanInvalid(
            f"Materialized provider identity does not match lock authority for {authority.bin_name}"
        )
    bins = payload.get("bin")
    if isinstance(bins, str):
        bins = {expected_name.rsplit("/", 1)[-1]: bins}
    if not isinstance(bins, dict) or bins.get(authority.bin_name) != authority.bin_target:
        raise FleetPlanInvalid(
            f"Materialized provider bin metadata does not match lock authority for "
            f"{authority.bin_name}"
        )


def _contained_regular_file(workspace: Path, relative: str, *, label: str) -> Path:
    parts = _safe_relative(relative, label)
    lexical = workspace.joinpath(*parts.parts)
    if lexical.is_symlink():
        raise FleetPlanInvalid(f"{label} cannot be a symlink: {relative}")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise FleetPlanInvalid(f"{label} is missing: {relative}") from exc
    _require_within(workspace, resolved, label)
    if not resolved.is_file():
        raise FleetPlanInvalid(f"{label} is not a regular file: {relative}")
    return resolved


def _safe_relative(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FleetPlanInvalid(f"Unsafe {label}: {value!r}")
    return path


def _require_within(root: Path, path: Path, label: str) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise FleetPlanInvalid(f"{label} escapes its admitted materialization root") from exc


def _step_working_directory(root: Path, step: ResolvedCommandStep) -> Path:
    value = getattr(step, "working_directory", ".")
    candidate = (root / value).resolve()
    _require_within(root, candidate, f"step {step.step_id} working directory")
    return candidate


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise FleetPlanInvalid(f"Cannot read execution-authority file: {path}") from exc
    return "sha256:" + digest.hexdigest(), size


def _package_name(package_path: str) -> str:
    relative = package_path.removeprefix("node_modules/")
    if relative.startswith("@"):
        parts = relative.split("/", 2)
        return "/".join(parts[:2])
    return relative.split("/", 1)[0]


def _display(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _snapshot_payload(identity: SnapshotIdentity) -> dict[str, object]:
    return {
        "sha256": identity.sha256,
        "byte_count": identity.byte_count,
        "entry_count": identity.entry_count,
    }


def _require_resolved(
    step: ResolvedCommandStep,
    unresolved: tuple[str, ...],
    owner: str,
) -> None:
    if not unresolved:
        return
    details = "; ".join(unresolved[:3])
    raise FleetPlanInvalid(
        f"Fleet plan step {step.step_id} execution authority for {owner} is unproven: {details}"
    )


def _file_receipt_payload(value: FileMaterializationReceipt) -> dict[str, object]:
    return {
        "relative_path": value.relative_path,
        "sha256": value.sha256,
        "byte_count": value.byte_count,
    }
