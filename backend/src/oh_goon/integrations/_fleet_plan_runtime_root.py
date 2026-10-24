from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import require_absolute_host_path
from oh_goon.core.path_layouts import SANDBOX_PATHS
from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
from oh_goon.integrations.content_snapshot import (
    SnapshotIdentity,
    snapshot_host_path,
    snapshot_identity,
    verify_snapshot,
)
from oh_goon.integrations.content_snapshot_dereferenced import snapshot_directory_dereferenced
from oh_goon.integrations.fleet_plan_managed_runtime import (
    ManagedRuntimeSpec,
    UvProjectManagedRuntime,
)
from oh_goon.integrations.uv_python_runtime import find_uv_system_python

_LDD_ABSOLUTE = re.compile(r"(?:=>\s*)?(/[^\s(]+)")
_PYTHON_NAME = re.compile(r"^python(?:\d+(?:\.\d+)?)?$", re.IGNORECASE)
_RUNTIME_DIRECTORY_EXCLUSIONS: dict[Path, tuple[str, ...]] = {
    Path("/etc/ssl").resolve(): ("private",),
}


@dataclass(frozen=True, slots=True)
class RuntimeRootBinding:
    """Sparse immutable host runtime closure mounted beneath strict CI/CD processes."""

    root: Path
    identity: SnapshotIdentity
    uv_system_python: PurePosixPath | None = None


def capture_runtime_root(
    host_tools: Iterable[HostToolBinding],
    managed_runtimes: Mapping[str, ManagedRuntimeSpec],
    destination: Path,
    *,
    runtime_inspector: Path,
    network_enabled: bool,
    managed_mount_targets: Iterable[PurePosixPath] = (),
    cancel_requested: Callable[[], bool] | None,
) -> RuntimeRootBinding:
    """Freeze transitive runtime bytes without exposing live host runtime directories."""

    host_tools = tuple(host_tools)
    files: set[Path] = set()
    directories: set[Path] = set()
    executable_sources: set[Path] = set()
    runtime_aliases: dict[PurePosixPath, Path] = {}
    uv_python = _managed_uv_system_python(managed_runtimes)
    uv_system_python = PurePosixPath(str(uv_python)) if uv_python is not None else None
    for binding in host_tools:
        source = Path(binding.source_path).resolve()
        executable_sources.add(source)
        if getattr(binding, "runtime_support", None) is None:
            files.update(
                _dynamic_dependencies(source, runtime_inspector, runtime_aliases=runtime_aliases)
            )
        name = str(binding.name)
        if _PYTHON_NAME.match(name):
            python_directories = _python_runtime_directories(source)
            directories.update(python_directories)
            files.update(
                _python_runtime_native_dependencies(
                    python_directories,
                    runtime_inspector,
                    runtime_aliases=runtime_aliases,
                )
            )
        elif name == "git":
            directories.update(_git_runtime_directories(source))
        elif name == "uv":
            python = uv_python if uv_python is not None else find_uv_system_python(source)
            if python is not None:
                uv_system_python = PurePosixPath(str(python))
                resolved_python = python.resolve()
                # uv's discovered system Python is runtime support for uv, not a
                # separately mounted /toolchain/bin host tool. Keep the interpreter
                # executable in the frozen root alongside its stdlib/libraries.
                files.add(resolved_python)
                if resolved_python != python:
                    runtime_aliases[PurePosixPath(str(python))] = resolved_python
                files.update(
                    _dynamic_dependencies(
                        resolved_python, runtime_inspector, runtime_aliases=runtime_aliases
                    )
                )
                python_directories = _python_runtime_directories(resolved_python)
                directories.update(python_directories)
                files.update(
                    _python_runtime_native_dependencies(
                        python_directories,
                        runtime_inspector,
                        runtime_aliases=runtime_aliases,
                    )
                )

    for spec in managed_runtimes.values():
        for python in _managed_python_interpreters(spec):
            executable_sources.add(python)
            files.update(
                _dynamic_dependencies(python, runtime_inspector, runtime_aliases=runtime_aliases)
            )
            python_directories = _python_runtime_directories(python)
            directories.update(python_directories)
            files.update(
                _python_runtime_native_dependencies(
                    python_directories,
                    runtime_inspector,
                    runtime_aliases=runtime_aliases,
                )
            )

    files.update(_nss_runtime_files(files))
    if network_enabled:
        for value in ("/etc/resolv.conf", "/etc/services", "/etc/protocols"):
            _include_runtime_file_preserving_exact_path(
                Path(value), files=files, runtime_aliases=runtime_aliases
            )
        ssl = Path("/etc/ssl")
        if ssl.is_dir():
            directories.add(ssl)

    # Top-level host executables live in /toolchain/bin, not the runtime root.
    files.difference_update(executable_sources)
    _materialize_sparse_root(
        files,
        directories,
        destination,
        directory_mount_targets=managed_mount_targets,
        file_mount_targets=_runtime_file_mount_targets(host_tools),
        file_aliases=tuple(sorted(runtime_aliases.items(), key=lambda item: str(item[0]))),
        cancel_requested=cancel_requested,
    )
    _materialize_host_tool_runtime_support(
        host_tools,
        destination,
        cancel_requested=cancel_requested,
    )
    identity = snapshot_identity(destination, cancel_requested=cancel_requested)
    return RuntimeRootBinding(
        root=destination.resolve(),
        identity=identity,
        uv_system_python=uv_system_python,
    )


def verify_runtime_root(
    binding: RuntimeRootBinding,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    verify_snapshot(
        binding.root,
        binding.identity,
        label="Fleet frozen execution runtime root",
        cancel_requested=cancel_requested,
    )


def _dynamic_dependencies(
    executable: Path,
    runtime_inspector: Path,
    *,
    runtime_aliases: dict[PurePosixPath, Path] | None = None,
    _seen: set[Path] | None = None,
) -> set[Path]:
    aliases = runtime_aliases if runtime_aliases is not None else {}
    seen = _seen if _seen is not None else set()
    executable = executable.resolve()
    if executable in seen:
        return set()
    seen.add(executable)
    try:
        with executable.open("rb") as handle:
            header = handle.read(512)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Cannot inspect Fleet host executable runtime: {executable}"
        ) from exc
    if not header.startswith(b"\x7fELF"):
        interpreter = _shebang_interpreter(header)
        if interpreter is None:
            return set()
        interpreter_source = require_absolute_host_path(
            interpreter, label=f"{executable.name} shebang interpreter"
        ).resolve()
        if not interpreter_source.is_file():
            raise FleetPlanInvalid(
                f"Strict Fleet shebang interpreter is unavailable: {interpreter}"
            )
        alias = PurePosixPath(interpreter)
        if str(interpreter_source) != interpreter:
            aliases[alias] = interpreter_source
        return {
            interpreter_source,
            *_dynamic_dependencies(
                interpreter_source,
                runtime_inspector,
                runtime_aliases=aliases,
                _seen=seen,
            ),
        }
    if not runtime_inspector.is_file():
        raise FleetPlanInvalid(
            "Strict Fleet runtime dependency inspector disappeared after deployment validation: "
            f"{runtime_inspector}"
        )
    completed = subprocess.run(
        (str(runtime_inspector), str(executable)),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"},
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    lowered = output.lower()
    if completed.returncode != 0 and not any(
        token in lowered for token in ("not a dynamic executable", "statically linked")
    ):
        raise FleetPlanInvalid(
            f"Strict Fleet CI/CD cannot determine runtime libraries for {executable.name}: {output.strip()}"
        )
    result: set[Path] = set()
    missing: list[str] = []
    for match in _LDD_ABSOLUTE.findall(output):
        runtime_path = Path(match)
        if not runtime_path.is_absolute():
            continue
        resolved = runtime_path.resolve()
        if not resolved.is_file():
            missing.append(str(runtime_path))
            continue
        result.add(resolved)
        if str(resolved) != str(runtime_path):
            aliases[PurePosixPath(str(runtime_path))] = resolved
    if missing:
        raise FleetPlanInvalid(
            "Fleet runtime dependency discovery returned missing files: "
            + ", ".join(sorted(missing))
        )
    return result


def _shebang_interpreter(header: bytes) -> str | None:
    if not header.startswith(b"#!"):
        return None
    first_line = header.splitlines()[0][2:].decode("utf-8", errors="strict").strip()
    if not first_line:
        raise FleetPlanInvalid("Fleet host tool has an empty shebang interpreter")
    interpreter = first_line.split(maxsplit=1)[0]
    if not interpreter.startswith("/"):
        raise FleetPlanInvalid(
            f"Fleet host tool shebang interpreter must be absolute: {interpreter!r}"
        )
    return interpreter


def _python_runtime_directories(executable: Path) -> set[Path]:
    script = (
        "import json,sysconfig; "
        "print(json.dumps({k:sysconfig.get_path(k) for k in ('stdlib','platstdlib')}))"
    )
    completed = subprocess.run(
        (str(executable), "-I", "-c", script),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "1"},
    )
    if completed.returncode != 0:
        raise FleetPlanInvalid(
            f"Strict Fleet CI/CD cannot discover Python runtime for {executable}: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    import json

    try:
        values = json.loads(completed.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise FleetPlanInvalid(
            f"Python runtime discovery returned invalid data for {executable}"
        ) from exc
    return {
        Path(value).resolve()
        for value in values.values()
        if isinstance(value, str) and Path(value).is_dir()
    }


def _python_runtime_native_dependencies(
    runtime_directories: Iterable[Path],
    runtime_inspector: Path,
    *,
    runtime_aliases: dict[PurePosixPath, Path],
) -> set[Path]:
    """Freeze shared-library closure for native modules copied with Python stdlib.

    The interpreter's own ldd closure is insufficient: stdlib extension modules such
    as ``_ssl`` are loaded later with dlopen and may depend on libraries that the
    interpreter binary itself never links.  Inspect every native ``*.so`` module in
    the exact Python runtime directories already selected for the frozen runtime.
    """

    result: set[Path] = set()
    observed: set[Path] = set()
    for root in _minimal_directories(runtime_directories):
        for candidate in sorted(root.rglob("*.so"), key=str):
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise FleetPlanInvalid(
                    f"Strict Fleet Python native runtime entry cannot be resolved: {candidate}"
                ) from exc
            if resolved in observed or not resolved.is_file():
                continue
            observed.add(resolved)
            result.update(
                _dynamic_dependencies(
                    resolved,
                    runtime_inspector,
                    runtime_aliases=runtime_aliases,
                )
            )
    return result


def _git_runtime_directories(executable: Path) -> set[Path]:
    completed = subprocess.run(
        (str(executable), "--exec-path"),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"},
    )
    if completed.returncode != 0:
        raise FleetPlanInvalid(
            f"Strict Fleet CI/CD cannot discover Git runtime helpers: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    path = Path(completed.stdout.strip()).resolve()
    return {path} if path.is_dir() else set()


def _managed_python_interpreters(spec: ManagedRuntimeSpec) -> set[Path]:
    result: set[Path] = set()
    for root in spec.readonly_paths:
        candidates = (
            root / "bin" / "python",
            root / "bin" / "python3",
            root / "Scripts" / "python.exe",
        )
        for candidate in candidates:
            if candidate.exists():
                result.add(candidate.resolve())
                break
    return result


def _managed_uv_system_python(
    managed_runtimes: Mapping[str, ManagedRuntimeSpec],
) -> Path | None:
    selected = {
        spec.python
        for spec in managed_runtimes.values()
        if isinstance(spec, UvProjectManagedRuntime)
    }
    if len(selected) > 1:
        raise FleetPlanInvalid(
            "Strict uv managed runtimes disagree on their selected system Python authority"
        )
    return next(iter(selected), None)


def _nss_runtime_files(dynamic_files: Iterable[Path]) -> set[Path]:
    result: set[Path] = set()
    parents = {path.parent for path in dynamic_files}
    for parent in parents:
        for pattern in ("libnss_*.so*", "libresolv.so*"):
            result.update(path.resolve() for path in parent.glob(pattern) if path.is_file())
    return result


def _include_runtime_file_preserving_exact_path(
    path: Path,
    *,
    files: set[Path],
    runtime_aliases: dict[PurePosixPath, Path],
) -> None:
    """Freeze file bytes and preserve the exact pathname expected inside the namespace."""

    if not path.exists():
        return
    resolved = path.resolve()
    files.add(resolved)
    if resolved != path:
        runtime_aliases[PurePosixPath(str(path))] = resolved


def _materialize_sparse_root(
    files: set[Path],
    directories: set[Path],
    destination: Path,
    *,
    directory_mount_targets: Iterable[PurePosixPath] = (),
    file_mount_targets: Iterable[PurePosixPath] = (),
    file_aliases: Iterable[tuple[PurePosixPath, Path]] = (),
    cancel_requested: Callable[[], bool] | None,
) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _create_mount_scaffold(
        destination,
        directory_mount_targets=directory_mount_targets,
        file_mount_targets=file_mount_targets,
    )
    resolved_directories = _minimal_directories(directories)
    for source in resolved_directories:
        target = destination / source.relative_to("/")
        snapshot_directory_dereferenced(
            source,
            target,
            label=f"Fleet runtime directory {source}",
            cancel_requested=cancel_requested,
            excluded_entries=_RUNTIME_DIRECTORY_EXCLUSIONS.get(source, ()),
        )
    for source in sorted({path.resolve() for path in files}, key=str):
        if any(source == root or source.is_relative_to(root) for root in resolved_directories):
            continue
        target = destination / source.relative_to("/")
        snapshot_host_path(
            source,
            target,
            label=f"Fleet runtime file {source.name}",
            cancel_requested=cancel_requested,
        )
    for sandbox_path, source in file_aliases:
        if not sandbox_path.is_absolute():
            raise FleetPlanInvalid(f"Fleet runtime file alias must be absolute: {sandbox_path}")
        target = destination / Path(*sandbox_path.parts[1:])
        snapshot_host_path(
            source,
            target,
            label=f"Fleet runtime alias {sandbox_path}",
            cancel_requested=cancel_requested,
        )


def _minimal_directories(paths: Iterable[Path]) -> tuple[Path, ...]:
    resolved = sorted(
        {path.resolve() for path in paths if path.is_dir()}, key=lambda p: len(p.parts)
    )
    result: list[Path] = []
    for path in resolved:
        if any(path == parent or path.is_relative_to(parent) for parent in result):
            continue
        result.append(path)
    return tuple(result)


def _materialize_host_tool_runtime_support(
    host_tools: Iterable[HostToolBinding],
    destination: Path,
    *,
    cancel_requested: Callable[[], bool] | None,
) -> None:
    """Copy already-frozen non-standalone tool support into the immutable runtime root."""

    observed: set[PurePosixPath] = set()
    for binding in host_tools:
        support = getattr(binding, "runtime_support", None)
        if support is None or support.sandbox_path in observed:
            continue
        observed.add(support.sandbox_path)
        verify_snapshot(
            support.snapshot_path,
            support.identity,
            label=f"frozen host tool runtime support {support.sandbox_path}",
            cancel_requested=cancel_requested,
        )
        target = destination / Path(*support.sandbox_path.parts[1:])
        copied = snapshot_directory_dereferenced(
            support.snapshot_path,
            target,
            label=f"Fleet runtime support {support.sandbox_path}",
            cancel_requested=cancel_requested,
        )
        if copied != support.identity:
            raise FleetPlanInvalid(
                f"Fleet runtime support identity changed while materializing {support.sandbox_path}"
            )


def _runtime_file_mount_targets(host_tools: Iterable[HostToolBinding]) -> tuple[PurePosixPath, ...]:
    fixed = tuple(
        SANDBOX_PATHS.etc / name for name in ("passwd", "group", "nsswitch.conf", "hosts")
    )
    host_tools = tuple(host_tools)
    toolchain = tuple(SANDBOX_PATHS.toolchain / str(binding.name) for binding in host_tools)
    shell = (
        (PurePosixPath("/bin/sh"),)
        if any(str(binding.name) == "sh" for binding in host_tools)
        else ()
    )
    return fixed + toolchain + shell


def _create_mount_scaffold(
    root: Path,
    *,
    directory_mount_targets: Iterable[PurePosixPath],
    file_mount_targets: Iterable[PurePosixPath],
) -> None:
    fixed = (
        SANDBOX_PATHS.proc,
        SANDBOX_PATHS.dev,
        SANDBOX_PATHS.etc,
        SANDBOX_PATHS.workspace,
        SANDBOX_PATHS.home,
        SANDBOX_PATHS.tmp,
        SANDBOX_PATHS.cache,
        SANDBOX_PATHS.config,
        SANDBOX_PATHS.toolchain,
        SANDBOX_PATHS.managed,
        PurePosixPath("/sys/fs/cgroup"),
    )
    for target in fixed:
        (root / Path(*target.parts[1:])).mkdir(parents=True, exist_ok=True)
    for target in directory_mount_targets:
        if not target.is_absolute():
            raise FleetPlanInvalid(f"Fleet runtime mount target must be absolute: {target}")
        (root / Path(*target.parts[1:])).mkdir(parents=True, exist_ok=True)
    for target in file_mount_targets:
        if not target.is_absolute():
            raise FleetPlanInvalid(f"Fleet runtime mount target must be absolute: {target}")
        destination = root / Path(*target.parts[1:])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not destination.is_file():
            raise FleetPlanInvalid(f"Fleet runtime file mount target is not a file: {target}")
        destination.touch(exist_ok=True)
