from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from oh_goon.engine.command import run_command
from oh_goon.runtime import exec_guard, exec_guard_protocol


@pytest.fixture
def owned_exec_guard_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[int, int], None, None]:
    reader, writer = os.pipe()
    try:
        # Popen pass_fds transfers an inheritable handle; the guard must clear it.
        os.set_inheritable(writer, True)
        monkeypatch.setenv(exec_guard_protocol.EXEC_ERROR_FD_ENV, str(writer))
        monkeypatch.delenv(exec_guard_protocol.OBSERVED_EXECUTABLE_ENV, raising=False)
        with Path(sys.executable).open("rb") as executable:
            monkeypatch.setenv(exec_guard_protocol.EXEC_FD_ENV, str(executable.fileno()))
            yield reader, writer
    finally:
        os.close(reader)
        os.close(writer)


def _literal_owners(value: str) -> set[Path]:
    source_root = Path(__file__).resolve().parents[3] / "src" / "oh_goon"
    owners: set[Path] = set()
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(isinstance(node, ast.Constant) and node.value == value for node in ast.walk(tree)):
            owners.add(path.resolve())
    return owners


@pytest.mark.parametrize(
    "identity",
    exec_guard_protocol.ENVIRONMENT,
)
def test_exec_guard_environment_identity_has_one_source_owner(identity: str) -> None:
    assert _literal_owners(identity) == {Path(exec_guard_protocol.__file__).resolve()}


@pytest.mark.parametrize("identity", exec_guard_protocol.ENVIRONMENT)
def test_ordinary_command_strips_exec_guard_protocol_fields(
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    case_variant = identity.lower()
    monkeypatch.setenv(case_variant, "ambient-control-value")
    probe = (
        "import os,sys; key=sys.argv[1]; "
        "print(any(k.casefold()==key.casefold() for k in os.environ))"
    )

    ambient = run_command(
        sys.executable,
        "-S",
        "-c",
        probe,
        identity,
    )
    explicit = run_command(
        sys.executable,
        "-S",
        "-c",
        probe,
        identity,
        env={
            "PATH": os.environ.get("PATH", ""),
            case_variant: "explicit-control-value",
        },
    )

    assert ambient.stdout == "False"
    assert explicit.stdout == "False"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sealed execution authority")
def test_owned_exec_guard_receives_parent_and_exact_admitted_execution_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_env: dict[str, str] = {}
    observed_pass_fds: tuple[int, ...] = ()
    error_pipe_identity: tuple[int, int] | None = None
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        nonlocal observed_env, observed_pass_fds, error_pipe_identity
        observed_env = dict(kwargs["env"])
        observed_pass_fds = tuple(kwargs["pass_fds"])
        error_pipe = os.fstat(int(observed_env[exec_guard_protocol.EXEC_ERROR_FD_ENV]))
        error_pipe_identity = (error_pipe.st_dev, error_pipe.st_ino)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import json,os\n"
        "objects=[]\n"
        "for name in os.listdir('/proc/self/fd'):\n"
        " try: value=os.fstat(int(name))\n"
        " except OSError: continue\n"
        " objects.append([value.st_dev,value.st_ino])\n"
        "print(json.dumps({'objects':objects,'guard_fields_exported':"
        "any(name.startswith('OH_GOON_EXEC_GUARD_') for name in os.environ)}))\n",
        parent_death_signal=True,
        preserve_native_executable_path=False,
        max_capture_bytes_per_stream=8192,
    )

    assert result.returncode == 0
    assert result.runtime_authority is not None
    assert result.runtime_authority.execution_materialization_identity is not None
    assert observed_env[exec_guard_protocol.PARENT_PID_ENV] == str(os.getpid())
    execution_fd = int(observed_env[exec_guard_protocol.EXEC_FD_ENV])
    assert execution_fd in observed_pass_fds
    error_fd = int(observed_env[exec_guard_protocol.EXEC_ERROR_FD_ENV])
    assert error_fd >= 3 and error_fd != execution_fd and error_fd in observed_pass_fds
    assert error_pipe_identity is not None
    workload = json.loads(result.stdout)
    assert workload["guard_fields_exported"] is False
    assert list(error_pipe_identity) not in workload["objects"]
    assert result.supervisor.final_process_group_quiescent


@pytest.mark.parametrize("raw_parent", [None, "not-a-pid", "0", "-1"])
def test_exec_guard_parent_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    raw_parent: str | None,
    owned_exec_guard_channel: tuple[int, int],
) -> None:
    monkeypatch.setattr(exec_guard, "_ensure_owned_session", lambda: None)
    if raw_parent is None:
        monkeypatch.delenv(exec_guard_protocol.PARENT_PID_ENV, raising=False)
    else:
        monkeypatch.setenv(exec_guard_protocol.PARENT_PID_ENV, raw_parent)

    with pytest.raises(SystemExit, match="exec guard parent identity is (missing|invalid)"):
        exec_guard.main(["tool"])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sealed execution authority")
@pytest.mark.parametrize("raw_exec_fd", [None, "not-an-fd", "-1"])
def test_exec_guard_execution_descriptor_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    raw_exec_fd: str | None,
    owned_exec_guard_channel: tuple[int, int],
) -> None:
    monkeypatch.setattr(exec_guard, "_ensure_owned_session", lambda: None)
    monkeypatch.setattr(exec_guard, "_set_parent_death_signal", lambda **_: None)
    monkeypatch.setenv(exec_guard_protocol.PARENT_PID_ENV, str(os.getppid()))
    if raw_exec_fd is None:
        monkeypatch.delenv(exec_guard_protocol.EXEC_FD_ENV, raising=False)
    else:
        monkeypatch.setenv(exec_guard_protocol.EXEC_FD_ENV, raw_exec_fd)

    with pytest.raises(SystemExit, match="exec guard execution descriptor is (missing|invalid)"):
        exec_guard.main(["tool"])
    assert os.get_inheritable(owned_exec_guard_channel[1]) is False


@pytest.mark.parametrize("raw_error_fd", [None, "not-an-fd", "-1", "0", "1", "2"])
def test_exec_guard_rejects_missing_or_nonprivate_startup_error_channel_before_exec(
    monkeypatch: pytest.MonkeyPatch,
    raw_error_fd: str | None,
    owned_exec_guard_channel: tuple[int, int],
) -> None:
    monkeypatch.setattr(exec_guard, "_ensure_owned_session", lambda: None)
    monkeypatch.setenv(exec_guard_protocol.PARENT_PID_ENV, str(os.getppid()))
    if raw_error_fd is None:
        monkeypatch.delenv(exec_guard_protocol.EXEC_ERROR_FD_ENV, raising=False)
    else:
        monkeypatch.setenv(exec_guard_protocol.EXEC_ERROR_FD_ENV, raw_error_fd)

    def reject_later_work(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("invalid startup error channel reached execution setup")

    monkeypatch.setattr(exec_guard, "_set_parent_death_signal", reject_later_work)
    monkeypatch.setattr(exec_guard, "_attach_controlling_terminal", reject_later_work)
    monkeypatch.setattr(exec_guard, "_start_parent_guard", reject_later_work)
    monkeypatch.setattr(os, "execve", reject_later_work)
    monkeypatch.setattr(os, "execvpe", reject_later_work)
    with pytest.raises(SystemExit, match="exec guard startup error channel is (missing|invalid)"):
        exec_guard.main(["tool"])
    assert os.get_inheritable(owned_exec_guard_channel[1]) is True


@pytest.mark.parametrize("terminal_request", ["0", "invalid", "1"])
def test_exec_guard_rejects_unowned_controlling_terminal(
    monkeypatch: pytest.MonkeyPatch, terminal_request: str
) -> None:
    monkeypatch.setenv(exec_guard_protocol.CONTROLLING_TERMINAL_ENV, terminal_request)
    monkeypatch.setattr(os, "isatty", lambda descriptor: False)

    with pytest.raises(SystemExit, match="exec guard controlling terminal is invalid"):
        exec_guard._attach_controlling_terminal()


@pytest.mark.parametrize(
    ("path", "execution_selected"), [("", False), ("relative", False), ("/native/program", True)]
)
def test_exec_guard_observational_mode_requires_one_absolute_path_without_a_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    execution_selected: bool,
    owned_exec_guard_channel: tuple[int, int],
) -> None:
    monkeypatch.setattr(exec_guard, "_ensure_owned_session", lambda: None)
    monkeypatch.setattr(exec_guard, "_set_parent_death_signal", lambda **kwargs: None)
    monkeypatch.setattr(exec_guard, "_attach_controlling_terminal", lambda: None)
    monkeypatch.setenv(exec_guard_protocol.PARENT_PID_ENV, str(os.getppid()))
    monkeypatch.setenv(exec_guard_protocol.OBSERVED_EXECUTABLE_ENV, path)
    if not execution_selected:
        monkeypatch.delenv(exec_guard_protocol.EXEC_FD_ENV, raising=False)
    monkeypatch.setattr(os, "execvpe", lambda *args: pytest.fail("invalid mode executed a path"))
    monkeypatch.setattr(
        os, "execve", lambda *args: pytest.fail("invalid mode executed a descriptor")
    )

    with pytest.raises(SystemExit, match="exec guard observational execution path is invalid"):
        exec_guard.main(["native-program"])
    assert os.get_inheritable(owned_exec_guard_channel[1]) is False
