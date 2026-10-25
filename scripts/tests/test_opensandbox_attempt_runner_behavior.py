from __future__ import annotations

import fcntl
import time
import sys

import io
import json
import os
import shutil
import signal
import stat
import subprocess
import tarfile
import warnings
from pathlib import Path

import pytest

from oh_goon.repository_tooling import opensandbox_attempt_runner as runner
from oh_goon.integrations.fleet_plan_opensandbox_isolation import _RUNNER_PROTOCOL
from oh_goon.repository_tooling._opensandbox_attempt_io import (
    archive_tree,
    extract_tree,
)
from oh_goon.runtime.process_group import process_group_exists


def _spawn_stubborn_descendant_group(tmp_path: Path) -> tuple[int, int, int, int, Path]:
    marker = tmp_path / "workspace" / "stale.txt"
    marker.parent.mkdir(exist_ok=True)
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    control_r, control_w = os.pipe()
    ready_r, ready_w = os.pipe()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"This process .* is multi-threaded, use of fork\(\) may lead to deadlocks.*",
            category=DeprecationWarning,
        )
        leader = os.fork()
    if leader == 0:
        os.close(stdout_r)
        os.close(stderr_r)
        os.setsid()
        os.dup2(stdout_w, 1)
        os.dup2(stderr_w, 2)
        os.close(stdout_w)
        os.close(stderr_w)
        descendant = os.fork()
        if descendant == 0:
            os.close(ready_r)
            os.close(control_w)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            marker.write_bytes(b"EARLY\n")
            os.write(ready_w, b"1")
            os.close(ready_w)
            while os.read(control_r, 1):
                with marker.open("ab", buffering=0) as output:
                    output.write(b"LATE\n")
            os._exit(0)
        os.close(ready_w)
        os.close(control_r)
        os.close(control_w)
        os.read(ready_r, 1)
        os.close(ready_r)
        os._exit(0)

    os.close(stdout_w)
    os.close(stderr_w)
    os.close(control_r)
    os.close(ready_r)
    os.close(ready_w)
    return leader, stdout_r, stderr_r, control_w, marker


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _tree_archive(tmp_path: Path, *, label: str | None = None, **files: str) -> str:
    root = tmp_path / f"archive-{len(list(tmp_path.glob('archive-*')))}"
    root.mkdir()
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    input_root = tmp_path / "input"
    input_root.mkdir(exist_ok=True)
    name = label or f"tree-{len(list(input_root.glob('*.tar.gz')))}"
    archive = input_root / f"{name}.tar.gz"
    archive_tree(root, archive)
    return archive.relative_to(tmp_path).as_posix()


def _base_payload(tmp_path: Path) -> dict[str, object]:
    runtime_root = _tree_archive(tmp_path, label="runtime-root")
    return {
        "protocol": runner._PROTOCOL,
        "runtime_root_archive": runtime_root,
        "workspace_archive": _tree_archive(
            tmp_path, label="workspace", **{"input.txt": "workspace\n"}
        ),
        "cache_archive": _tree_archive(
            tmp_path, label="cache", **{"cached.txt": "cache\n"}
        ),
        "cache_read_only": False,
        "toolchain": [],
        "readonly_inputs": [],
        "etc_files": {},
        "argv": ["tool"],
        "cwd": "/workspace",
        "env": {"PATH": "/toolchain/bin"},
        "max_output_bytes_per_stream": 1024 * 1024,
        "egress": {"mode": "none"},
    }


def test_path_and_timeout_helpers_fail_closed() -> None:
    assert runner._timeout_text(b"bad-\xff") == "bad-�"
    assert runner._timeout_text(None) == ""
    assert runner._inside(Path("/tmp/root"), "/workspace/a") == Path(
        "/tmp/root/workspace/a"
    )
    with pytest.raises(ValueError, match="normalized absolute"):
        runner._inside(Path("/tmp/root"), "workspace/a")
    with pytest.raises(ValueError, match="normalized absolute"):
        runner._inside(Path("/tmp/root"), "/workspace/../escape")


def test_extract_tree_rejects_archive_escape(tmp_path: Path) -> None:
    archive_path = tmp_path / "escape.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        data = b"escape"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError, match="escapes destination"):
        extract_tree(archive_path, tmp_path / "destination")
    assert not (tmp_path / "escape.txt").exists()


def test_readonly_inputs_support_file_tree_and_reject_reserved_or_unknown(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    input_root = tmp_path / "input"
    input_root.mkdir()
    config = input_root / "config.txt"
    config.write_text("config", encoding="utf-8")
    runner._write_readonly(
        root,
        {
            "declared_path": "/inputs/config.txt",
            "kind": "file",
            "source": "input/config.txt",
            "mode": 0o400,
        },
    )
    assert (root / "inputs/config.txt").read_text(encoding="utf-8") == "config"
    assert stat.S_IMODE((root / "inputs/config.txt").stat().st_mode) == 0o400

    tree = _tree_archive(tmp_path, **{"a.txt": "A"})
    runner._write_readonly(
        root,
        {"declared_path": "/inputs/tree", "kind": "tree", "source": tree},
    )
    assert (root / "inputs/tree/a.txt").read_text(encoding="utf-8") == "A"

    with pytest.raises(ValueError, match="collides with reserved path"):
        runner._write_readonly(
            root,
            {
                "declared_path": "/workspace/hostile",
                "kind": "file",
                "source": "input/config.txt",
            },
        )
    with pytest.raises(ValueError, match="unknown kind"):
        runner._write_readonly(
            root,
            {
                "declared_path": "/inputs/other",
                "kind": "device",
                "source": "input/config.txt",
            },
        )


def test_prepare_root_replaces_old_bytes_builds_shell_and_validates_etc(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "old.txt").write_text("old", encoding="utf-8")
    payload = _base_payload(tmp_path)
    payload["cache_read_only"] = True
    tool = tmp_path / "input" / "sh"
    tool.write_bytes(b"#!/bin/sh\n")
    payload["toolchain"] = [
        {
            "name": "sh",
            "source": "input/sh",
            "mode": 0o555,
        }
    ]
    payload["etc_files"] = {"hosts": "127.0.0.1 localhost\n"}

    runner._prepare_root(payload, root)

    assert not (root / "old.txt").exists()
    assert (root / "bin/sh").is_symlink()
    assert os.readlink(root / "bin/sh") == "/toolchain/bin/sh"
    assert (root / "etc/hosts").read_text(encoding="utf-8").startswith("127.0.0.1")
    assert not ((root / "cache/cached.txt").stat().st_mode & stat.S_IWUSR)

    invalid = _base_payload(tmp_path)
    invalid["etc_files"] = {"nested/name": "bad"}
    with pytest.raises(ValueError, match="etc file name is invalid"):
        runner._prepare_root(invalid, tmp_path / "invalid-root")


def test_runtime_path_and_executable_resolution_cover_absolute_and_path_modes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    binary = root / "toolchain/bin/tool"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")

    assert runner._resolve_runtime_path(root, "/toolchain/bin/tool") == binary.resolve()
    with pytest.raises(ValueError, match="missing"):
        runner._resolve_runtime_path(root, "/toolchain/bin/missing")
    directory = root / "toolchain/bin/directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="not a file"):
        runner._resolve_runtime_path(root, "/toolchain/bin/directory")
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    (root / "toolchain/bin/escape").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes root"):
        runner._resolve_runtime_path(root, "/toolchain/bin/escape")

    assert (
        runner._runtime_executable(
            {"argv": ["/toolchain/bin/tool"], "env": {"PATH": ""}}, root
        )[0]
        == "/toolchain/bin/tool"
    )
    assert (
        runner._runtime_executable(
            {"argv": ["tool"], "env": {"PATH": "/missing:/toolchain/bin"}}, root
        )[0]
        == "/toolchain/bin/tool"
    )
    with pytest.raises(ValueError, match="identify an executable"):
        runner._runtime_executable({"argv": [], "env": {}}, root)
    with pytest.raises(ValueError, match="relative executable path is not normalized"):
        runner._runtime_executable({"argv": ["bin/tool"], "env": {}}, root)
    with pytest.raises(ValueError, match="env must be a mapping"):
        runner._runtime_executable({"argv": ["tool"], "env": []}, root)
    with pytest.raises(ValueError, match="absent from PATH"):
        runner._runtime_executable(
            {"argv": ["missing"], "env": {"PATH": "/toolchain/bin"}}, root
        )


def test_runtime_file_and_memfd_validation_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "tool"
    file_path.write_bytes(b"payload")
    assert runner._file_identity(file_path).startswith("sha256:")

    directory = tmp_path / "directory"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="not a regular file"):
            runner._sealed_runtime_copy(descriptor, label="directory")
    finally:
        os.close(descriptor)

    source = os.open(file_path, os.O_RDONLY)
    try:
        monkeypatch.delattr(os, "memfd_create")
        with pytest.raises(ValueError, match="requires Linux memfd sealing"):
            runner._sealed_runtime_copy(source, label="tool")
    finally:
        os.close(source)


def test_write_and_seal_helpers_detect_kernel_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "write", lambda _fd, _data: 0)
    with pytest.raises(OSError, match="short write"):
        runner._write_all(123, b"payload", error="short write")

    monkeypatch.setattr(fcntl, "fcntl", lambda *_args: 0)
    with pytest.raises(ValueError, match="not fully sealed"):
        runner._seal_descriptor(123, label="runtime")


def test_shebang_and_argv_validation_are_explicit(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid"
    invalid_utf8.write_bytes(b"#!\xff\n")
    fd = os.open(invalid_utf8, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="invalid shebang"):
            runner._fd_shebang(fd)
    finally:
        os.close(fd)

    relative = tmp_path / "relative"
    relative.write_bytes(b"#!python -u\n")
    fd = os.open(relative, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="unbound shebang interpreter"):
            runner._fd_shebang(fd)
    finally:
        os.close(fd)

    with pytest.raises(ValueError, match="non-empty string array"):
        runner._exec_argv({"argv": ["ok", 1]}, {"executable_fd": 5})
    with pytest.raises(ValueError, match="string mapping"):
        runner._runtime_environment({"env": {"GOOD": 1}})


def test_restore_interpreter_validates_binding_before_filesystem_write() -> None:
    runner._restore_atomic_interpreter({"interpreter_fd": None})
    with pytest.raises(ValueError, match="resolved path is missing"):
        runner._restore_atomic_interpreter(
            {"interpreter_fd": 7, "interpreter_resolved_path": None}
        )


def test_close_runtime_binding_is_idempotent_for_closed_descriptors(
    tmp_path: Path,
) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"x")
    descriptor = os.open(path, os.O_RDONLY)
    os.close(descriptor)
    runner._close_runtime_binding({"executable_fd": descriptor, "interpreter_fd": None})


def test_deadline_and_process_group_helpers_classify_live_and_gone_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    assert runner._terminate_if_deadline_expired(44, None) is False
    assert runner._terminate_if_deadline_expired(44, 11.0) is False

    waited: list[int] = []
    monkeypatch.setattr(
        os, "killpg", lambda *_args: (_ for _ in ()).throw(ProcessLookupError())
    )
    monkeypatch.setattr(runner, "_wait_for_pid", lambda pid: waited.append(pid))
    assert runner._terminate_if_deadline_expired(44, 9.0) is True
    assert waited == [44]

    monkeypatch.setattr(runner, "process_group_exists", lambda _pid: True)
    monkeypatch.setattr(
        os, "killpg", lambda *_args: (_ for _ in ()).throw(PermissionError())
    )
    with pytest.raises(runner._AttemptQuiescenceError, match="could not signal"):
        runner._settle_remaining_group(44)


def test_collect_child_reports_missing_status_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    os.close(w1)
    os.close(w2)
    monkeypatch.setattr(os, "waitpid", lambda *_args: (0, 0))
    monkeypatch.setattr(runner, "_terminate_if_deadline_expired", lambda *_args: True)
    monkeypatch.setattr(runner, "_settle_atomic_child", lambda _pid, _readers: None)
    with pytest.raises(subprocess.TimeoutExpired):
        runner._collect_atomic_child(77, ["tool"], 0.01, r1, r2)

    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    os.close(w1)
    os.close(w2)
    monkeypatch.setattr(runner, "_terminate_if_deadline_expired", lambda *_args: False)
    calls = iter([(77, 0)])
    monkeypatch.setattr(os, "waitpid", lambda *_args: next(calls))
    completed = runner._collect_atomic_child(77, ["tool"], None, r1, r2)
    assert completed.returncode == 0


def test_collect_child_fails_closed_when_remote_output_crosses_capture_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    os.write(stdout_w, b"x" * 64)
    os.close(stdout_w)
    os.close(stderr_w)
    monkeypatch.setattr(os, "waitpid", lambda *_args: (0, 0))
    monkeypatch.setattr(os, "killpg", lambda *_args: None)
    monkeypatch.setattr(runner, "_wait_for_pid", lambda _pid: None)
    monkeypatch.setattr(runner, "_settle_atomic_child", lambda _pid, _readers: None)

    with pytest.raises(RuntimeError, match="stdout exceeded the 32-byte capture limit"):
        runner._collect_atomic_child(
            77,
            ["tool"],
            None,
            stdout_r,
            stderr_r,
            max_output_bytes_per_stream=32,
        )


@pytest.mark.skipif(os.name == "nt", reason="OpenSandbox attempt runner is POSIX-only")
def test_collect_child_proves_stubborn_descendant_group_quiescent_before_return(
    tmp_path: Path,
) -> None:
    leader, stdout_r, stderr_r, control_w, marker = _spawn_stubborn_descendant_group(
        tmp_path
    )
    try:
        completed = runner._collect_atomic_child(
            leader, ["tool"], None, stdout_r, stderr_r
        )
        assert completed.returncode == 0
        assert process_group_exists(leader) is False
        assert marker.read_bytes() == b"EARLY\n"
        with pytest.raises(BrokenPipeError):
            os.write(control_w, b"W")
        assert marker.read_bytes() == b"EARLY\n"
    finally:
        os.close(control_w)
        _kill_process_group(leader)


@pytest.mark.skipif(os.name == "nt", reason="OpenSandbox attempt runner is POSIX-only")
def test_workspace_archive_is_cut_only_after_stubborn_descendant_is_quiescent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "cache").mkdir()
    leader, stdout_r, stderr_r, control_w, marker = _spawn_stubborn_descendant_group(
        root
    )
    try:
        completed = runner._collect_atomic_child(
            leader, ["tool"], None, stdout_r, stderr_r
        )
        assert completed.returncode == 0
        archives = runner._archive_result_trees(root)
        with pytest.raises(BrokenPipeError):
            os.write(control_w, b"W")
        assert marker.read_bytes() == b"EARLY\n"
        archive = root.parent / archives["workspace_archive"]
        with tarfile.open(archive, mode="r:gz") as payload:
            extracted = payload.extractfile("stale.txt")
            assert extracted is not None
            assert extracted.read() == b"EARLY\n"
    finally:
        os.close(control_w)
        _kill_process_group(leader)


def test_execute_attempt_does_not_archive_when_quiescence_cannot_be_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    monkeypatch.setattr(runner, "_prepare_root", lambda _payload, _root: None)
    monkeypatch.setattr(
        runner, "_prepare_workload_filesystem", lambda _payload, _root: None
    )
    monkeypatch.setattr(
        runner,
        "_bind_runtime_execution",
        lambda _payload, _root: {"authority": {}},
    )
    monkeypatch.setattr(runner, "_close_runtime_binding", lambda _binding: None)

    def unsafe_run(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise runner._AttemptQuiescenceError("survivors-detected")

    def forbidden_archive(_root: Path) -> dict[str, str]:
        pytest.fail("unsettled attempt must not archive mutable workspace/cache")

    monkeypatch.setattr(runner, "_run", unsafe_run)
    monkeypatch.setattr(runner, "_archive_result_trees", forbidden_archive)
    with pytest.raises(runner._AttemptQuiescenceError, match="survivors-detected"):
        runner._execute_attempt({}, root)


def test_unexpected_post_launch_error_cannot_publish_mutable_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_prepare_root", lambda _payload, _root: None)
    monkeypatch.setattr(
        runner, "_prepare_workload_filesystem", lambda _payload, _root: None
    )
    monkeypatch.setattr(
        runner, "_bind_runtime_execution", lambda _payload, _root: {"authority": {}}
    )
    closed: list[object] = []
    monkeypatch.setattr(runner, "_close_runtime_binding", closed.append)

    def failed_run(*_args: object) -> None:
        raise OSError("unexpected native launch failure")

    def forbidden_archive(_root: Path) -> dict[str, str]:
        pytest.fail("uncertain native launch cannot publish mutable outputs")

    monkeypatch.setattr(runner, "_run", failed_run)
    monkeypatch.setattr(runner, "_archive_result_trees", forbidden_archive)
    with pytest.raises(OSError, match="unexpected native launch failure"):
        runner._execute_attempt({}, tmp_path / "root")
    assert closed == [{"authority": {}}]


def test_result_archives_and_failure_observation_are_explicit(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert runner._archive_result_trees(root) == {
        "workspace_archive": "output/workspace.tar.gz",
        "cache_archive": "output/cache.tar.gz",
    }
    assert (tmp_path / "output/workspace.tar.gz").is_file()
    assert (tmp_path / "output/cache.tar.gz").is_file()
    assert runner._observation_field(None) == {}
    assert runner._observation_field({"executable_path": "/tool"}) == {
        "runtime_authority": {"executable_path": "/tool"}
    }


def test_execute_attempt_fail_closed_before_runtime_binding(tmp_path: Path) -> None:
    payload = _base_payload(tmp_path)
    payload["runtime_root_archive"] = "input/missing.tar.gz"
    result = runner._execute_attempt(payload, tmp_path / "root")
    assert result["returncode"] == 125
    assert "Fleet OpenSandbox runner failed closed" in result["stderr"]
    assert "runtime_authority" not in result


def test_main_validates_protocol_egress_and_writes_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit, match="usage"):
        monkeypatch.setattr(sys, "argv", ["runner"])
        runner.main()

    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(
        json.dumps({"protocol": 99, "egress": {"mode": "none"}}), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["runner", str(request), str(result)])
    with pytest.raises(ValueError, match="unsupported Fleet runner protocol"):
        runner.main()

    request.write_text(
        json.dumps({"protocol": runner._PROTOCOL, "egress": {"mode": "allow"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="deny-all egress only"):
        runner.main()

    request.write_text(
        json.dumps({"protocol": _RUNNER_PROTOCOL, "egress": {"mode": "none"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "_execute_attempt",
        lambda payload, root: {"returncode": 0, "stdout": "ok", "stderr": ""},
    )
    assert runner.main() == 0
    assert json.loads(result.read_text(encoding="utf-8")) == {
        "protocol": runner._PROTOCOL,
        "returncode": 0,
        "stdout": "ok",
        "stderr": "",
    }


def test_unprivileged_helper_refuses_before_workload_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(ValueError, match="VM-root custody"):
        runner._prepare_workload_filesystem({}, root)


def test_privilege_drop_failure_prevents_native_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_identity() -> None:
        raise PermissionError("identity drop refused")

    def forbidden_exec(*_args: object) -> None:
        os._exit(99)

    monkeypatch.setattr(os, "chroot", lambda _root: None)
    monkeypatch.setattr(runner, "_restore_atomic_interpreter", lambda _binding: None)
    monkeypatch.setattr(runner, "_drop_workload_privileges", refuse_identity)
    monkeypatch.setattr(runner, "_fexecve", forbidden_exec)
    pid, stdout, stderr = runner._fork_atomic_child(
        root=tmp_path,
        cwd=str(tmp_path),
        env={},
        binding={},
        descriptor=-1,
        exec_argv=["tool"],
    )
    result = runner._collect_atomic_child(pid, ["tool"], 5, stdout, stderr)
    assert result.returncode == 125
    assert "identity drop refused" in result.stderr


@pytest.mark.certification
@pytest.mark.skipif(
    os.geteuid() != 0, reason="Native VM helper identity proof requires root"
)
def test_native_unprivileged_workload_cannot_mutate_immutable_inputs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    payload = _base_payload(tmp_path)
    runtime = tmp_path / "native-runtime"
    runtime.mkdir()
    shell = Path("/bin/sh").resolve(strict=True)
    targets = [shell]
    linked = subprocess.run(
        ["ldd", str(shell)], check=True, capture_output=True, text=True
    )
    for token in linked.stdout.split():
        if token.startswith("/"):
            targets.append(Path(token))
    for source in targets:
        destination = runtime / source.relative_to("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o555)
    shell_target = runtime / "bin/sh"
    shell_target.parent.mkdir(parents=True, exist_ok=True)
    if shell_target != runtime / shell.relative_to("/"):
        shutil.copyfile(shell, shell_target)
        shell_target.chmod(0o555)
    archive = tmp_path / "input/native-runtime.tar.gz"
    archive_tree(runtime, archive)
    payload["runtime_root_archive"] = "input/native-runtime.tar.gz"
    source_archive = _tree_archive(tmp_path, **{"source.txt": "captured-source"})
    payload["readonly_inputs"] = [
        {"declared_path": "/managed/source", "kind": "tree", "source": source_archive}
    ]
    payload["argv"] = [
        "/bin/sh",
        "-c",
        "set -eu; "
        "if printf changed > /managed/source/source.txt 2>/dev/null; then exit 10; fi; "
        "if printf replaced > /managed/new-source 2>/dev/null; then exit 11; fi; "
        "if printf changed > /bin/sh 2>/dev/null; then exit 12; fi; "
        "printf application > /workspace/result.txt; "
        "printf cache > /cache/prepared.txt; "
        "printf home > /home/fleet/owned.txt; "
        "printf temporary > /tmp/owned.txt; "
        "printf configuration > /config/owned.txt; "
        "printf portable > /dev/null",
    ]
    result = runner._execute_attempt(payload, root)
    assert result["returncode"] == 0, result["stderr"]
    assert (root / "managed/source/source.txt").read_text() == "captured-source"
    assert (root / "workspace/result.txt").read_text() == "application"
    assert (root / "workspace/result.txt").stat().st_uid == 65534
    assert (root / "managed/source/source.txt").stat().st_uid == 0


@pytest.mark.certification
@pytest.mark.skipif(
    os.geteuid() != 0, reason="Native VM helper build proof requires root"
)
@pytest.mark.parametrize("build_backend", ("in-tree", "uv-build"))
def test_native_helper_builds_and_runs_captured_python_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_backend: str
) -> None:
    from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
    from oh_goon.integrations._fleet_plan_opensandbox_transport import (
        _restore_result_archives,
    )
    from oh_goon.integrations._fleet_plan_runtime_root import capture_runtime_root
    from oh_goon.integrations.content_snapshot import snapshot_identity
    from oh_goon.integrations.executable_materialization_authority import (
        StepExecutableMaterializationReceipt,
    )
    from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime
    from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
    from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
    from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
    from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
    from oh_goon.core.models.fleet_plan_step_contract import DeniedPlanEgress
    from oh_goon.core.path_layouts import AttemptPathLayout
    from tests.factories.python_goon_projects import write_isolated_build_project

    monkeypatch.setenv("UV_PYTHON", sys.executable)
    project = tmp_path / "captured-project"
    write_isolated_build_project(project)
    if build_backend == "uv-build":
        metadata = project / "pyproject.toml"
        metadata.write_text(
            metadata.read_text().replace(
                'requires = []\nbuild-backend = "backend"\nbackend-path = ["."]',
                'requires = ["uv_build>=0.12,<0.13"]\nbuild-backend = "uv_build"\n'
                '[tool.uv.build-backend]\nmodule-name = "isolated_app"\nmodule-root = ""',
            )
        )
        package = project / "isolated_app"
        package.mkdir()
        shutil.copyfile(project / "isolated_app.py", package / "__init__.py")
    uv_name = shutil.which("uv")
    ldd_name = shutil.which("ldd")
    assert uv_name is not None and ldd_name is not None
    uv = Path(uv_name).resolve(strict=True)
    base_executable = getattr(sys, "_base_executable", None)
    assert isinstance(base_executable, str)
    python = Path(base_executable).resolve(strict=True)
    payload = _base_payload(tmp_path)
    runtime = tmp_path / "native-runtime"
    tool = HostToolBinding("uv", uv, uv, snapshot_identity(uv))
    runtime_binding = capture_runtime_root(
        (tool,),
        {"build": UvProjectManagedRuntime(".", None, tmp_path / "cache", python)},
        runtime,
        runtime_inspector=Path(ldd_name).resolve(strict=True),
        network_enabled=False,
        cancel_requested=None,
    )
    archive_tree(runtime, tmp_path / "input/native-runtime.tar.gz")
    payload["runtime_root_archive"] = "input/native-runtime.tar.gz"
    shutil.copyfile(uv, tmp_path / "input/uv")
    payload["toolchain"] = [{"name": "uv", "source": "input/uv", "mode": 0o555}]
    cache = tmp_path / "input-cache"
    cache.mkdir()
    shutil.copytree(project, cache / "uv-build-project")
    archive_tree(cache, tmp_path / "input/native-cache.tar.gz")
    payload["cache_archive"] = "input/native-cache.tar.gz"
    archive_tree(project, tmp_path / "input/source.tar.gz")
    payload["readonly_inputs"] = [
        {
            "declared_path": "/managed/source",
            "kind": "tree",
            "source": "input/source.tar.gz",
        }
    ]
    payload["env"] = {
        "PATH": "/toolchain/bin",
        "HOME": "/home/fleet",
        "TMPDIR": "/tmp",
        "UV_PYTHON": str(python),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_MANAGED_PYTHON": "1",
        "UV_PROJECT_ENVIRONMENT": "/cache/uv-project-environment",
        "UV_CACHE_DIR": "/cache/uv",
        "UV_OFFLINE": "1",
    }
    payload["argv"] = [
        "/toolchain/bin/uv",
        "sync",
        "--locked",
        "--no-default-groups",
        "--no-editable",
        "--link-mode",
        "copy",
        "--project",
        "/cache/uv-build-project",
    ]
    root = tmp_path / "root"
    result = runner._execute_attempt(payload, root)
    assert result["returncode"] == 0, result["stderr"]
    if build_backend == "in-tree":
        assert json.loads(
            (root / "cache/uv-build-project/backend-build.json").read_text()
        ) == {"private_write": True}
    assert not (root / "managed/source/backend-build.json").exists()
    assert not (project / "backend-build.json").exists()
    receiving = AttemptPathLayout.from_root(tmp_path / "receiving-attempt")
    receiving.workspace.mkdir(parents=True)
    receiving.cache.mkdir()
    attempt = FleetPlanStepAttempt(
        paths=receiving,
        cache_identity=snapshot_identity(receiving.cache),
        egress=DeniedPlanEgress(),
        host_tools=(tool,),
        sandbox_bindings=(),
        managed_runtime=None,
        host_environment={},
        runtime_root=runtime_binding,
        isolation_backend=PlanIsolationBackendEvidence(
            backend_id="native-helper-test",
            configuration_id="native-helper-test",
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
        resources=CicdResourcePolicy(),
        nested_strict_execution=False,
        executable_materialization=StepExecutableMaterializationReceipt(),
        attempt_number=1,
    )
    _restore_result_archives(
        IsolatedProcessRequest(attempt, ("uv",), receiving.workspace, {}),
        {"protocol": _RUNNER_PROTOCOL, **result},
        {
            name: root.parent / str(result[name])
            for name in ("workspace_archive", "cache_archive")
        },
    )
    interpreter = receiving.cache / "uv-project-environment/bin/python"
    assert interpreter.is_file() and not interpreter.is_symlink()
    assert interpreter.read_bytes() == python.read_bytes()
    # The next provider process consumes exactly the validated restored cache.
    archive_tree(receiving.cache, tmp_path / "input/restored-cache.tar.gz")
    payload["cache_archive"] = "input/restored-cache.tar.gz"
    payload["argv"] = [
        "/toolchain/bin/uv",
        "run",
        "--no-sync",
        "--project",
        "/cache/uv-build-project",
        "/cache/uv-project-environment/bin/isolated-build",
    ]
    application = runner._execute_attempt(payload, root)
    assert application["returncode"] == 0, application["stderr"]
    assert json.loads((root / "workspace/isolated-result.json").read_text())[
        "dev_dependency_absent"
    ]
