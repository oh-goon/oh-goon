from __future__ import annotations

import io
import json
import os
import signal
import stat
import subprocess
import tarfile
import warnings
from pathlib import Path

import pytest

from oh_goon.repository_tooling import opensandbox_attempt_runner as runner


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
    runner._archive_tree(root, archive)
    return archive.relative_to(tmp_path).as_posix()


def _base_payload(tmp_path: Path) -> dict[str, object]:
    runtime_root = _tree_archive(tmp_path, label="runtime-root")
    return {
        "protocol": runner._PROTOCOL,
        "runtime_root_archive": runtime_root,
        "workspace_archive": _tree_archive(
            tmp_path, label="workspace", **{"input.txt": "workspace\n"}
        ),
        "cache_archive": _tree_archive(tmp_path, label="cache", **{"cached.txt": "cache\n"}),
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
    assert runner._inside(Path("/tmp/root"), "/workspace/a") == Path("/tmp/root/workspace/a")
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
        runner._extract_tree(archive_path, tmp_path / "destination")
    assert not (tmp_path / "escape.txt").exists()


def test_readonly_inputs_support_file_tree_and_reject_reserved_or_unknown(tmp_path: Path) -> None:
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
            {"declared_path": "/workspace/hostile", "kind": "file", "source": "input/config.txt"},
        )
    with pytest.raises(ValueError, match="unknown kind"):
        runner._write_readonly(
            root,
            {"declared_path": "/inputs/other", "kind": "device", "source": "input/config.txt"},
        )


def test_prepare_root_replaces_old_bytes_builds_shell_and_validates_etc(tmp_path: Path) -> None:
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
        runner._runtime_executable({"argv": ["/toolchain/bin/tool"], "env": {"PATH": ""}}, root)[0]
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
        runner._runtime_executable({"argv": ["missing"], "env": {"PATH": "/toolchain/bin"}}, root)


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
        monkeypatch.delattr(runner.os, "memfd_create")
        with pytest.raises(ValueError, match="requires Linux memfd sealing"):
            runner._sealed_runtime_copy(source, label="tool")
    finally:
        os.close(source)


def test_write_and_seal_helpers_detect_kernel_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.os, "write", lambda _fd, _data: 0)
    with pytest.raises(OSError, match="short write"):
        runner._write_all(123, b"payload", error="short write")

    monkeypatch.setattr(runner.fcntl, "fcntl", lambda *_args: 0)
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
        runner._restore_atomic_interpreter({"interpreter_fd": 7, "interpreter_resolved_path": None})


def test_close_runtime_binding_is_idempotent_for_closed_descriptors(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"x")
    descriptor = os.open(path, os.O_RDONLY)
    os.close(descriptor)
    runner._close_runtime_binding({"executable_fd": descriptor, "interpreter_fd": None})


def test_deadline_and_process_group_helpers_classify_live_and_gone_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.time, "monotonic", lambda: 10.0)
    assert runner._terminate_if_deadline_expired(44, None) is False
    assert runner._terminate_if_deadline_expired(44, 11.0) is False

    waited: list[int] = []
    monkeypatch.setattr(
        runner.os, "killpg", lambda *_args: (_ for _ in ()).throw(ProcessLookupError())
    )
    monkeypatch.setattr(runner, "_wait_for_pid", lambda pid: waited.append(pid))
    assert runner._terminate_if_deadline_expired(44, 9.0) is True
    assert waited == [44]

    monkeypatch.setattr(runner, "process_group_exists", lambda _pid: True)
    monkeypatch.setattr(
        runner.os, "killpg", lambda *_args: (_ for _ in ()).throw(PermissionError())
    )
    with pytest.raises(runner._AttemptQuiescenceError, match="could not signal"):
        runner._settle_remaining_group(44)


def test_collect_child_reports_missing_status_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    os.close(w1)
    os.close(w2)
    monkeypatch.setattr(runner.os, "waitpid", lambda *_args: (0, 0))
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
    monkeypatch.setattr(runner.os, "waitpid", lambda *_args: next(calls))
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
    monkeypatch.setattr(runner.os, "waitpid", lambda *_args: (0, 0))
    monkeypatch.setattr(runner.os, "killpg", lambda *_args: None)
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
    leader, stdout_r, stderr_r, control_w, marker = _spawn_stubborn_descendant_group(tmp_path)
    try:
        completed = runner._collect_atomic_child(leader, ["tool"], None, stdout_r, stderr_r)
        assert completed.returncode == 0
        assert runner.process_group_exists(leader) is False
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
    leader, stdout_r, stderr_r, control_w, marker = _spawn_stubborn_descendant_group(root)
    try:
        completed = runner._collect_atomic_child(leader, ["tool"], None, stdout_r, stderr_r)
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
        runner,
        "_bind_runtime_execution",
        lambda _payload, _root: {"authority": {}},
    )
    monkeypatch.setattr(runner, "_close_runtime_binding", lambda _binding: None)

    def unsafe_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise runner._AttemptQuiescenceError("survivors-detected")

    def forbidden_archive(_root: Path) -> dict[str, str]:
        pytest.fail("unsettled attempt must not archive mutable workspace/cache")

    monkeypatch.setattr(runner, "_run", unsafe_run)
    monkeypatch.setattr(runner, "_archive_result_trees", forbidden_archive)
    with pytest.raises(runner._AttemptQuiescenceError, match="survivors-detected"):
        runner._execute_attempt({}, root)


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
        monkeypatch.setattr(runner.sys, "argv", ["runner"])
        runner.main()

    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(json.dumps({"protocol": 99, "egress": {"mode": "none"}}), encoding="utf-8")
    monkeypatch.setattr(runner.sys, "argv", ["runner", str(request), str(result)])
    with pytest.raises(ValueError, match="unsupported Fleet runner protocol"):
        runner.main()

    request.write_text(
        json.dumps({"protocol": runner._PROTOCOL, "egress": {"mode": "allow"}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="deny-all egress only"):
        runner.main()

    request.write_text(
        json.dumps({"protocol": runner._PROTOCOL, "egress": {"mode": "none"}}), encoding="utf-8"
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
