"""Fixed OpenSandbox attempt helper; user argv is transported only as JSON."""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling._opensandbox_attempt_io import (
    ByteCapture as _ByteCapture,
)
from oh_goon.repository_tooling._opensandbox_attempt_io import (
    archive_tree as _archive_tree,
)
from oh_goon.repository_tooling._opensandbox_attempt_io import (
    complete_child_result as _complete_child_result,
)
from oh_goon.repository_tooling._opensandbox_attempt_io import (
    extract_tree as _extract_tree,
)
from oh_goon.repository_tooling._opensandbox_attempt_io import (
    fleet_file as _fleet_file,
)
from oh_goon.repository_tooling._opensandbox_attempt_io import (
    read_pipe as _read_pipe,
)
from oh_goon.runtime.process_group import (
    process_group_exists,
    wait_for_process_group_quiescence,
)

_PROTOCOL = 3
_RESERVED = ("/workspace", "/cache", "/home/fleet", "/tmp", "/config", "/toolchain")
_PROCESS_GROUP_TERM_GRACE_SECONDS = 1.0
_PROCESS_GROUP_KILL_SETTLEMENT_SECONDS = 1.0
_OUTPUT_READER_SETTLEMENT_SECONDS = 2.0


class _AttemptQuiescenceError(RuntimeError):
    """The attempt cannot publish terminal evidence because owned work is unsettled."""


def _timeout_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _inside(root: Path, absolute: str) -> Path:
    path = Path(absolute)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Fleet runner path must be normalized absolute: {absolute!r}")
    return root / path.relative_to("/")


def _write_readonly(root: Path, item: dict[str, Any]) -> None:
    declared = str(item["declared_path"])
    if declared == "/" or any(
        declared == value or declared.startswith(value + "/") for value in _RESERVED
    ):
        raise ValueError(f"Fleet runner readonly input collides with reserved path: {declared}")
    destination = _inside(root, declared)
    source = _fleet_file(root, str(item["source"]))
    if item["kind"] == "tree":
        _extract_tree(source, destination)
    elif item["kind"] == "file":
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(int(item.get("mode", 0o444)))
    else:
        raise ValueError("Fleet runner readonly input has unknown kind")


def _make_tree_read_only(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in (*files, *directories):
            path = current_path / name
            if path.is_symlink():
                continue
            path.chmod(path.stat().st_mode & ~0o222)
        if not current_path.is_symlink():
            current_path.chmod(current_path.stat().st_mode & ~0o222)


def _prepare_root(payload: dict[str, Any], root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _extract_tree(_fleet_file(root, str(payload["runtime_root_archive"])), root)
    for path in ("workspace", "cache", "home/fleet", "tmp", "config", "toolchain/bin"):
        (root / path).mkdir(parents=True, exist_ok=True)
    _extract_tree(_fleet_file(root, str(payload["workspace_archive"])), root / "workspace")
    _extract_tree(_fleet_file(root, str(payload["cache_archive"])), root / "cache")
    if bool(payload.get("cache_read_only", False)):
        _make_tree_read_only(root / "cache")
    shell_declared = False
    for tool in payload.get("toolchain", []):
        name = str(tool["name"])
        destination = root / "toolchain" / "bin" / name
        shutil.copyfile(_fleet_file(root, str(tool["source"])), destination)
        destination.chmod(int(tool.get("mode", 0o555)))
        shell_declared = shell_declared or name == "sh"
    if shell_declared:
        shell = root / "bin" / "sh"
        shell.parent.mkdir(parents=True, exist_ok=True)
        shell.unlink(missing_ok=True)
        shell.symlink_to("/toolchain/bin/sh")
    for item in payload.get("readonly_inputs", []):
        _write_readonly(root, item)
    etc_files = payload.get("etc_files", {})
    if etc_files:
        etc = root / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        for name, text in etc_files.items():
            if "/" in name or "\\" in name:
                raise ValueError("Fleet runner etc file name is invalid")
            (etc / name).write_text(str(text), encoding="utf-8")


def _identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_identity(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _resolve_runtime_path(root: Path, sandbox_path: str) -> Path:
    candidate = _inside(root, sandbox_path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Fleet runner executable is missing: {sandbox_path}") from exc
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Fleet runner executable escapes root: {sandbox_path}")
    if not resolved.is_file():
        raise ValueError(f"Fleet runner executable is not a file: {sandbox_path}")
    return resolved


def _runtime_executable(payload: dict[str, Any], root: Path) -> tuple[str, Path]:
    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
        raise ValueError("Fleet runner argv must identify an executable")
    value = argv[0]
    candidate = Path(value)
    if candidate.is_absolute():
        return value, _resolve_runtime_path(root, value)
    if "/" in value or "\\" in value:
        raise ValueError("Fleet runner relative executable path is not normalized")
    env = payload.get("env")
    if not isinstance(env, dict):
        raise ValueError("Fleet runner env must be a mapping")
    for entry in str(env.get("PATH", "")).split(":"):
        if not entry:
            continue
        sandbox = str(Path(entry) / value)
        try:
            return sandbox, _resolve_runtime_path(root, sandbox)
        except ValueError:
            continue
    raise ValueError(f"Fleet runner executable is absent from PATH: {value}")


def _open_runtime_file(path: Path, *, label: str) -> int:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Fleet runner {label} cannot be resolved") from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError(f"Fleet runner {label} cannot be opened without final symlink") from exc
    try:
        return _sealed_runtime_copy(source, label=label)
    finally:
        os.close(source)


def _sealed_runtime_copy(source: int, *, label: str) -> int:
    metadata = os.fstat(source)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Fleet runner {label} is not a regular file")
    memfd_flags = getattr(os, "MFD_ALLOW_SEALING", 0)
    if not hasattr(os, "memfd_create") or not memfd_flags:
        raise ValueError("Fleet runner atomic execution requires Linux memfd sealing")
    memfd = os.memfd_create(f"oh-goon-{label}", flags=memfd_flags)
    try:
        _copy_descriptor(source, memfd, error="short write while sealing Fleet runtime file")
        os.fchmod(memfd, stat.S_IMODE(metadata.st_mode))
        _seal_descriptor(memfd, label=label)
        os.set_inheritable(memfd, True)
        return memfd
    except BaseException:
        os.close(memfd)
        raise


def _copy_descriptor(source: int, destination: int, *, error: str) -> None:
    offset = 0
    while chunk := os.pread(source, 1024 * 1024, offset):
        _write_all(destination, chunk, error=error)
        offset += len(chunk)


def _write_all(descriptor: int, data: bytes, *, error: str) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(error)
        view = view[written:]


def _seal_descriptor(descriptor: int, *, label: str) -> None:
    required = 0x000F  # Linux UAPI: WRITE | GROW | SHRINK | SEAL.
    fcntl.fcntl(descriptor, getattr(fcntl, "F_ADD_SEALS", 1033), required)
    applied = fcntl.fcntl(descriptor, getattr(fcntl, "F_GET_SEALS", 1034))
    if applied & required != required:
        raise ValueError(f"Fleet runner {label} memfd is not fully sealed")


def _fd_identity(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            break
        digest.update(chunk)
        offset += len(chunk)
    return "sha256:" + digest.hexdigest()


def _fd_shebang(descriptor: int) -> tuple[str, ...] | None:
    first = os.pread(descriptor, 4096, 0).splitlines()[0] if os.fstat(descriptor).st_size else b""
    if not first.startswith(b"#!"):
        return None
    try:
        parts = tuple(shlex.split(first[2:].decode("utf-8", errors="strict").strip()))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Fleet runner executable has invalid shebang") from exc
    if not parts or not Path(parts[0]).is_absolute():
        raise ValueError("Fleet runner executable has unbound shebang interpreter")
    return parts


def _bind_runtime_execution(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    executable_path, executable = _runtime_executable(payload, root)
    env = payload.get("env")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("Fleet runner env must be a string mapping")
    executable_fd = _open_runtime_file(executable, label="executable")
    interpreter_fd: int | None = None
    try:
        executable_identity = _fd_identity(executable_fd)
        shebang = _fd_shebang(executable_fd)
        interpreter_path: str | None = None
        interpreter_identity: str | None = None
        if shebang is not None:
            interpreter_path = shebang[0]
            interpreter = _resolve_runtime_path(root, interpreter_path)
            interpreter_fd = _open_runtime_file(interpreter, label="interpreter")
            interpreter_identity = _fd_identity(interpreter_fd)
            interpreter_resolved_path = "/" + interpreter.relative_to(root.resolve()).as_posix()
        if shebang is None:
            interpreter_resolved_path = None
        return {
            "authority": {
                "executable_path": executable_path,
                "executable_identity": executable_identity,
                "path_authority_identity": _identity({"PATH": env.get("PATH", "")}),
                "environment_identity": _identity({"environment": sorted(env.items())}),
                "interpreter_path": interpreter_path,
                "interpreter_identity": interpreter_identity,
            },
            "executable_fd": executable_fd,
            "interpreter_fd": interpreter_fd,
            "shebang": shebang,
            "executable_path": executable_path,
            "interpreter_resolved_path": interpreter_resolved_path,
        }
    except BaseException:
        os.close(executable_fd)
        if interpreter_fd is not None:
            os.close(interpreter_fd)
        raise


def _close_runtime_binding(binding: dict[str, Any]) -> None:
    for name in ("executable_fd", "interpreter_fd"):
        descriptor = binding.get(name)
        if isinstance(descriptor, int):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _fexecve(descriptor: int, argv: list[str], env: dict[str, str]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.fexecve
    function.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.POINTER(ctypes.c_char_p),
    ]
    function.restype = ctypes.c_int
    argv_bytes = [value.encode() for value in argv]
    env_bytes = [f"{key}={value}".encode() for key, value in env.items()]
    argv_array = (ctypes.c_char_p * (len(argv_bytes) + 1))(*argv_bytes, None)
    env_array = (ctypes.c_char_p * (len(env_bytes) + 1))(*env_bytes, None)
    function(descriptor, argv_array, env_array)
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error))


def _exec_argv(payload: dict[str, Any], binding: dict[str, Any]) -> tuple[int, list[str]]:
    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(value, str) for value in argv):
        raise ValueError("Fleet runner argv must be a non-empty string array")
    return int(binding["executable_fd"]), list(argv)


def _restore_atomic_interpreter(binding: dict[str, Any]) -> None:
    descriptor = binding.get("interpreter_fd")
    sandbox_path = binding.get("interpreter_resolved_path")
    if not isinstance(descriptor, int):
        return
    if not isinstance(sandbox_path, str) or not sandbox_path.startswith("/"):
        raise ValueError("Fleet runner interpreter resolved path is missing")
    destination = Path(sandbox_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".oh-goon-{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    try:
        _copy_descriptor(
            descriptor,
            output,
            error="short write while restoring atomic interpreter",
        )
        os.fchmod(output, stat.S_IMODE(os.fstat(descriptor).st_mode))
        os.fsync(output)
    finally:
        os.close(output)
    os.replace(temporary, destination)


def _run(
    payload: dict[str, Any], root: Path, binding: dict[str, Any]
) -> subprocess.CompletedProcess[str]:
    cwd = str(payload["cwd"])
    _inside(root, cwd)
    env = _runtime_environment(payload)
    timeout = float(payload["timeout"]) if payload.get("timeout") is not None else None
    max_output_bytes = int(payload.get("max_output_bytes_per_stream", 0))
    if max_output_bytes < 1:
        raise ValueError("Fleet runner requires a positive max_output_bytes_per_stream")
    descriptor, exec_argv = _exec_argv(payload, binding)
    pid, stdout_r, stderr_r = _fork_atomic_child(
        root=root,
        cwd=cwd,
        env=env,
        binding=binding,
        descriptor=descriptor,
        exec_argv=exec_argv,
    )
    return _collect_atomic_child(
        pid,
        exec_argv,
        timeout,
        stdout_r,
        stderr_r,
        max_output_bytes_per_stream=max_output_bytes,
    )


def _runtime_environment(payload: dict[str, Any]) -> dict[str, str]:
    env = payload.get("env")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("Fleet runner env must be a string mapping")
    return dict(env)


def _fork_atomic_child(
    *,
    root: Path,
    cwd: str,
    env: dict[str, str],
    binding: dict[str, Any],
    descriptor: int,
    exec_argv: list[str],
) -> tuple[int, int, int]:
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    # The production runner is a dedicated single-threaded helper. Pytest/plugin
    # harnesses may have unrelated host threads, which makes Python 3.12+ warn at
    # fork even though no runner-owned thread exists before this cut.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"This process .* is multi-threaded, use of fork\(\) may lead to deadlocks.*",
            category=DeprecationWarning,
        )
        pid = os.fork()
    if pid == 0:
        try:
            os.close(stdout_r)
            os.close(stderr_r)
            os.dup2(stdout_w, 1)
            os.dup2(stderr_w, 2)
            os.close(stdout_w)
            os.close(stderr_w)
            os.chroot(root)
            os.chdir(cwd)
            os.setsid()
            _restore_atomic_interpreter(binding)
            _fexecve(descriptor, exec_argv, env)
        except BaseException as exc:
            try:
                os.write(2, f"atomic exec failed: {type(exc).__name__}: {exc}\n".encode())
            finally:
                os._exit(125)
        raise AssertionError("unreachable")

    os.close(stdout_w)
    os.close(stderr_w)
    return pid, stdout_r, stderr_r


def _collect_atomic_child(
    pid: int,
    exec_argv: list[str],
    timeout: float | None,
    stdout_r: int,
    stderr_r: int,
    *,
    max_output_bytes_per_stream: int | None = None,
) -> subprocess.CompletedProcess[str]:
    overflow = threading.Event()
    stdout_capture = _ByteCapture(max_output_bytes_per_stream, overflow, "stdout")
    stderr_capture = _ByteCapture(max_output_bytes_per_stream, overflow, "stderr")
    readers = (
        threading.Thread(target=_read_pipe, args=(stdout_r, stdout_capture), daemon=True),
        threading.Thread(target=_read_pipe, args=(stderr_r, stderr_capture), daemon=True),
    )
    _start_readers(readers)
    deadline = None if timeout is None else time.monotonic() + timeout
    status: int | None = None
    timed_out = False
    output_limited = False
    try:
        while status is None:
            waited, observed = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status = observed
                break
            if overflow.is_set():
                output_limited = True
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _wait_for_pid(pid)
                break
            timed_out = _terminate_if_deadline_expired(pid, deadline)
            if timed_out:
                break
            time.sleep(0.01)
    finally:
        _settle_atomic_child(pid, readers)
    return _complete_child_result(
        exec_argv,
        timeout,
        status,
        timed_out=timed_out,
        output_limited=output_limited,
        stdout_capture=stdout_capture,
        stderr_capture=stderr_capture,
    )


def _start_readers(readers: tuple[threading.Thread, threading.Thread]) -> None:
    for reader in readers:
        reader.start()


def _join_readers(readers: tuple[threading.Thread, threading.Thread]) -> None:
    deadline = time.monotonic() + _OUTPUT_READER_SETTLEMENT_SECONDS
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        raise _AttemptQuiescenceError(
            "Fleet OpenSandbox output readers did not stop after process-group quiescence"
        )


def _settle_atomic_child(pid: int, readers: tuple[threading.Thread, threading.Thread]) -> None:
    settlement_error: _AttemptQuiescenceError | None = None
    try:
        _settle_remaining_group(pid)
    except _AttemptQuiescenceError as exc:
        settlement_error = exc
    try:
        _join_readers(readers)
    except _AttemptQuiescenceError as exc:
        if settlement_error is None:
            settlement_error = exc
    if settlement_error is not None:
        raise settlement_error


def _terminate_if_deadline_expired(pid: int, deadline: float | None) -> bool:
    if deadline is None or time.monotonic() < deadline:
        return False
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _wait_for_pid(pid)
    return True


def _wait_for_pid(pid: int) -> None:
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _settle_remaining_group(pid: int) -> None:
    if not process_group_exists(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise _AttemptQuiescenceError(
            "Fleet OpenSandbox could not signal the owned process group"
        ) from exc
    if wait_for_process_group_quiescence(pid, timeout=_PROCESS_GROUP_TERM_GRACE_SECONDS):
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise _AttemptQuiescenceError(
            "Fleet OpenSandbox could not kill the owned process group"
        ) from exc
    if not wait_for_process_group_quiescence(pid, timeout=_PROCESS_GROUP_KILL_SETTLEMENT_SECONDS):
        raise _AttemptQuiescenceError(
            "Fleet OpenSandbox owned process group did not become quiescent"
        )


def _archive_result_trees(root: Path) -> dict[str, str]:
    output = root.parent / "output"
    output.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace"
    cache = root / "cache"
    workspace.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    _archive_tree(workspace, output / "workspace.tar.gz")
    _archive_tree(cache, output / "cache.tar.gz")
    return {
        "workspace_archive": "output/workspace.tar.gz",
        "cache_archive": "output/cache.tar.gz",
    }


def _observation_field(
    runtime_authority: dict[str, str | None] | None,
) -> dict[str, object]:
    if runtime_authority is None:
        return {}
    return {"runtime_authority": runtime_authority}


def _execute_attempt(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    runtime_binding: dict[str, Any] | None = None
    runtime_authority: dict[str, str | None] | None = None
    try:
        _prepare_root(payload, root)
        runtime_binding = _bind_runtime_execution(payload, root)
        runtime_authority = dict(runtime_binding["authority"])
        completed = _run(payload, root, runtime_binding)
        return {
            "returncode": completed.returncode,
            "runtime_authority": runtime_authority,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            **_archive_result_trees(root),
        }
    except _AttemptQuiescenceError:
        # A mutable workspace/cache is not terminal evidence. Let the outer
        # sandbox owner destroy the disposable sandbox instead of archiving it.
        raise
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            **_observation_field(runtime_authority),
            "stdout": _timeout_text(exc.stdout),
            "stderr": _timeout_text(exc.stderr) + "\nFleet OpenSandbox attempt timed out",
            **_archive_result_trees(root),
        }
    except BaseException as exc:
        return {
            "returncode": 125,
            **_observation_field(runtime_authority),
            "stdout": "",
            "stderr": f"Fleet OpenSandbox runner failed closed: {type(exc).__name__}: {exc}",
            **_archive_result_trees(root),
        }
    finally:
        if runtime_binding is not None:
            _close_runtime_binding(runtime_binding)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: opensandbox_attempt_runner.py REQUEST RESULT")
    request_path, result_path = map(Path, sys.argv[1:])
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    if int(payload.get("protocol", -1)) != _PROTOCOL:
        raise ValueError("unsupported Fleet runner protocol")
    if payload.get("egress") != {"mode": "none"}:
        raise ValueError("experimental OpenSandbox runner accepts deny-all egress only")
    result = {"protocol": _PROTOCOL, **_execute_attempt(payload, Path("/fleet/rootfs"))}
    result_path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
