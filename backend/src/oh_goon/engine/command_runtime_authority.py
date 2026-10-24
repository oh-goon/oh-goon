from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from oh_goon.core import local_access_protocol
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.strict_executor_authority import STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT
from oh_goon.engine.atomic_execution_bytes import (
    SealedExecutionBytes,
    atomic_execution_bytes_supported,
    execution_materialization_identity,
    seal_execution_bytes,
    sealed_execution_shebang,
)
from oh_goon.runtime import exec_guard_protocol


def _non_exportable_process_environment() -> frozenset[str]:
    return frozenset(
        {
            "OH_GOON_DB",
            "OH_GOON_LEAGUE_FILE",
            local_access_protocol.LOCAL_ACCESS_ENV,
            "OH_GOON_PROGRESS_FD",
            *exec_guard_protocol.ENVIRONMENT,
            SQLITE_JOURNAL_MODE_ENV,
            *STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT,
        }
    )


def sanitized_child_environment(
    argv: Sequence[str], env: Mapping[str, str] | None
) -> dict[str, str]:
    """Remove control-plane authority before process birth and preserve explicit lookup authority."""
    child = dict(os.environ if env is None else env)
    blocked = {name.casefold() for name in _non_exportable_process_environment()}
    child = {name: value for name, value in child.items() if name.casefold() not in blocked}
    if env is not None and argv and _requires_path_lookup(argv[0]) and "PATH" not in child:
        raise ValueError(
            "Explicit command environment for a bare executable must carry PATH; "
            f"executable={argv[0]!r}"
        )
    return child


def _requires_path_lookup(executable_name: str) -> bool:
    candidate = Path(executable_name)
    return (
        not candidate.is_absolute() and "/" not in executable_name and "\\" not in executable_name
    )


def _sha_identity(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _require_sha256(name: str, value: str | None) -> None:
    if value is not None and (not value.startswith("sha256:") or len(value) != 71):
        raise ValueError(f"{name} must be a sha256 identity")


@dataclass(frozen=True, slots=True)
class RuntimeAuthorityReceipt:
    """Execution-owner observation of the authority actually admitted to a command."""

    executable_path: str
    executable_identity: str
    executable_byte_count: int
    path_authority_identity: str
    environment_identity: str
    provider_identity: str | None = None
    runtime_identity: str | None = None
    environment_generation_identity: str | None = None
    dependency_supply_identity: str | None = None
    interpreter_path: str | None = None
    interpreter_identity: str | None = None
    execution_materialization_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.executable_path.strip():
            raise ValueError("executable_path must be nonempty")
        if self.executable_byte_count < 0:
            raise ValueError("executable_byte_count must be non-negative")
        if self.interpreter_path is not None and not self.interpreter_path.strip():
            raise ValueError("interpreter_path must be nonempty when present")
        if (self.interpreter_path is None) != (self.interpreter_identity is None):
            raise ValueError("interpreter path and identity must be present together")
        for name in (
            "executable_identity",
            "path_authority_identity",
            "environment_identity",
            "provider_identity",
            "runtime_identity",
            "environment_generation_identity",
            "dependency_supply_identity",
            "interpreter_identity",
            "execution_materialization_identity",
        ):
            _require_sha256(name, getattr(self, name))

    @property
    def receipt_identity(self) -> str:
        return _sha_identity({"schema": "oh-goon.runtime-authority-receipt/v1", **asdict(self)})


@dataclass(frozen=True, slots=True)
class RuntimeAuthorityBinding:
    """Already-admitted non-host execution identities supplied by the execution owner."""

    provider_identity: str | None = None
    runtime_identity: str | None = None
    environment_generation_identity: str | None = None
    dependency_supply_identity: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "provider_identity",
            "runtime_identity",
            "environment_generation_identity",
            "dependency_supply_identity",
        ):
            _require_sha256(name, getattr(self, name))


@dataclass(slots=True)
class CommandRuntimeAuthorityLease:
    """Lifetime owner for exact local command execution bytes."""

    receipt: RuntimeAuthorityReceipt
    launch_argv: tuple[str, ...]
    execution_descriptor: int | None = None
    executable: SealedExecutionBytes | None = None
    interpreter: SealedExecutionBytes | None = None
    _closed: bool = False

    @property
    def pass_fds(self) -> tuple[int, ...]:
        values: list[int] = []
        if self.executable is not None:
            values.append(self.executable.descriptor)
        if self.interpreter is not None:
            values.append(self.interpreter.descriptor)
        return tuple(values)

    @property
    def execution_path(self) -> str | None:
        if self.execution_descriptor is None:
            return None
        return f"/proc/self/fd/{self.execution_descriptor}"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in self.pass_fds:
            try:
                os.close(descriptor)
            except OSError:
                pass


def bind_command_runtime_authority(
    argv: Sequence[str],
    child_env: Mapping[str, str],
    cwd: Path | None,
    binding: RuntimeAuthorityBinding | None,
    *,
    preserve_native_executable_path: bool = False,
) -> CommandRuntimeAuthorityLease:
    if not argv:
        raise ValueError("command argv must be nonempty")
    path_value = child_env.get("PATH", "")
    executable_path = _resolve_runtime_executable(argv[0], path_value, cwd)
    _require_runtime_executable_access(executable_path, display_name=argv[0])
    admitted = binding or RuntimeAuthorityBinding()
    if atomic_execution_bytes_supported() and not preserve_native_executable_path:
        return _bind_atomic_command_runtime(
            argv,
            child_env=child_env,
            executable_path=executable_path,
            path_value=path_value,
            admitted=admitted,
        )
    return CommandRuntimeAuthorityLease(
        receipt=_observed_runtime_authority(
            executable_path,
            child_env=child_env,
            path_value=path_value,
            admitted=admitted,
        ),
        launch_argv=tuple(argv),
    )


def _resolve_runtime_executable(
    executable_name: str,
    path_value: str,
    cwd: Path | None,
) -> Path:
    if _requires_path_lookup(executable_name):
        resolved = shutil.which(executable_name, path=path_value)
        if resolved is None:
            raise FileNotFoundError(
                errno.ENOENT,
                f"Executable not found under admitted PATH: {executable_name}",
                executable_name,
            )
        return Path(resolved).resolve()
    candidate = Path(executable_name)
    if not candidate.is_absolute():
        candidate = (cwd or Path.cwd()) / candidate
    return candidate.resolve()


def _bind_atomic_command_runtime(
    argv: Sequence[str],
    *,
    child_env: Mapping[str, str],
    executable_path: Path,
    path_value: str,
    admitted: RuntimeAuthorityBinding,
) -> CommandRuntimeAuthorityLease:
    executable = seal_execution_bytes(executable_path, label="command runtime executable")
    try:
        shebang = sealed_execution_shebang(executable, label="command runtime executable")
        if shebang is None:
            return _binary_runtime_lease(
                argv,
                child_env=child_env,
                executable_path=executable_path,
                executable=executable,
                path_value=path_value,
                admitted=admitted,
            )
        return _script_runtime_lease(
            argv,
            child_env=child_env,
            executable_path=executable_path,
            executable=executable,
            shebang=shebang,
            path_value=path_value,
            admitted=admitted,
        )
    except BaseException:
        try:
            os.close(executable.descriptor)
        except OSError:
            pass
        raise


def _binary_runtime_lease(
    argv: Sequence[str],
    *,
    child_env: Mapping[str, str],
    executable_path: Path,
    executable: SealedExecutionBytes,
    path_value: str,
    admitted: RuntimeAuthorityBinding,
) -> CommandRuntimeAuthorityLease:
    receipt = _runtime_authority_receipt(
        executable_path,
        executable.identity,
        executable.byte_count,
        child_env=child_env,
        path_value=path_value,
        admitted=admitted,
        execution_materialization_identity=execution_materialization_identity(executable, None),
    )
    return CommandRuntimeAuthorityLease(
        receipt=receipt,
        launch_argv=tuple(argv),
        execution_descriptor=executable.descriptor,
        executable=executable,
    )


def _script_runtime_lease(
    argv: Sequence[str],
    *,
    child_env: Mapping[str, str],
    executable_path: Path,
    executable: SealedExecutionBytes,
    shebang: tuple[str, ...],
    path_value: str,
    admitted: RuntimeAuthorityBinding,
) -> CommandRuntimeAuthorityLease:
    interpreter_path = Path(shebang[0])
    if not interpreter_path.is_absolute():
        return _fallback_from_sealed_observation(
            argv,
            child_env=child_env,
            executable_path=executable_path,
            executable=executable,
            path_value=path_value,
            admitted=admitted,
        )
    interpreter_path = interpreter_path.resolve()
    _require_runtime_executable_access(interpreter_path, display_name=str(interpreter_path))
    interpreter = seal_execution_bytes(
        interpreter_path,
        label="command runtime interpreter",
    )
    try:
        nested = sealed_execution_shebang(interpreter, label="command runtime interpreter")
        if nested is not None or interpreter_path.name == "env":
            return _fallback_from_sealed_observation(
                argv,
                child_env=child_env,
                executable_path=executable_path,
                executable=executable,
                path_value=path_value,
                admitted=admitted,
                interpreter_path=interpreter_path,
                interpreter=interpreter,
            )
        receipt = _runtime_authority_receipt(
            executable_path,
            executable.identity,
            executable.byte_count,
            child_env=child_env,
            path_value=path_value,
            admitted=admitted,
            interpreter_path=interpreter_path,
            interpreter_identity=interpreter.identity,
            execution_materialization_identity=execution_materialization_identity(
                executable, interpreter
            ),
        )
        return CommandRuntimeAuthorityLease(
            receipt=receipt,
            launch_argv=(
                # Native Python and other interpreters use their invocation path
                # for environment selection. Seal the resolved bytes, but retain
                # argv[0] from the sealed script's shebang (including a venv path).
                shebang[0],
                *shebang[1:],
                executable.proc_path,
                *argv[1:],
            ),
            execution_descriptor=interpreter.descriptor,
            executable=executable,
            interpreter=interpreter,
        )
    except BaseException:
        try:
            os.close(interpreter.descriptor)
        except OSError:
            pass
        raise


def _fallback_from_sealed_observation(
    argv: Sequence[str],
    *,
    child_env: Mapping[str, str],
    executable_path: Path,
    executable: SealedExecutionBytes,
    path_value: str,
    admitted: RuntimeAuthorityBinding,
    interpreter_path: Path | None = None,
    interpreter: SealedExecutionBytes | None = None,
) -> CommandRuntimeAuthorityLease:
    receipt = _runtime_authority_receipt(
        executable_path,
        executable.identity,
        executable.byte_count,
        child_env=child_env,
        path_value=path_value,
        admitted=admitted,
        interpreter_path=interpreter_path,
        interpreter_identity=interpreter.identity if interpreter is not None else None,
    )
    os.close(executable.descriptor)
    if interpreter is not None:
        os.close(interpreter.descriptor)
    return CommandRuntimeAuthorityLease(receipt=receipt, launch_argv=tuple(argv))


def _observed_runtime_authority(
    executable_path: Path,
    *,
    child_env: Mapping[str, str],
    path_value: str,
    admitted: RuntimeAuthorityBinding,
) -> RuntimeAuthorityReceipt:
    try:
        executable_sha256, executable_byte_count = sha256_file_with_size(executable_path)
    except OSError as exc:
        raise OSError(
            exc.errno,
            f"Cannot bind admitted executable bytes: {executable_path}",
            str(executable_path),
        ) from exc
    return _runtime_authority_receipt(
        executable_path,
        "sha256:" + executable_sha256,
        executable_byte_count,
        child_env=child_env,
        path_value=path_value,
        admitted=admitted,
    )


def _runtime_authority_receipt(
    executable_path: Path,
    executable_identity: str,
    executable_byte_count: int,
    *,
    child_env: Mapping[str, str],
    path_value: str,
    admitted: RuntimeAuthorityBinding,
    interpreter_path: Path | None = None,
    interpreter_identity: str | None = None,
    execution_materialization_identity: str | None = None,
) -> RuntimeAuthorityReceipt:
    return RuntimeAuthorityReceipt(
        executable_path=str(executable_path),
        executable_identity=executable_identity,
        executable_byte_count=executable_byte_count,
        path_authority_identity=_sha_identity({"PATH": path_value}),
        environment_identity=_sha_identity({"environment": sorted(child_env.items())}),
        provider_identity=admitted.provider_identity,
        runtime_identity=admitted.runtime_identity,
        environment_generation_identity=admitted.environment_generation_identity,
        dependency_supply_identity=admitted.dependency_supply_identity,
        interpreter_path=str(interpreter_path) if interpreter_path is not None else None,
        interpreter_identity=interpreter_identity,
        execution_materialization_identity=execution_materialization_identity,
    )


def _require_runtime_executable_access(path: Path, *, display_name: str) -> None:
    try:
        metadata = path.stat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            errno.ENOENT, f"Executable not found: {display_name}", display_name
        ) from exc
    except OSError as exc:
        raise OSError(exc.errno, exc.strerror, display_name) from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise PermissionError(
            errno.EACCES, f"Executable is not runnable: {display_name}", display_name
        )
