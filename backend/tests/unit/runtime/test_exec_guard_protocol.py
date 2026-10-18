from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.engine.command import run_command
from oh_goon.runtime import exec_guard, exec_guard_protocol


def _literal_owners(value: str) -> set[Path]:
    source_root = Path(__file__).resolve().parents[3] / "src" / "oh_goon"
    owners: set[Path] = set()
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Constant) and node.value == value
            for node in ast.walk(tree)
        ):
            owners.add(path.resolve())
    return owners


@pytest.mark.parametrize(
    "identity",
    [exec_guard_protocol.PARENT_PID_ENV, exec_guard_protocol.EXEC_FD_ENV],
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
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        nonlocal observed_env, observed_pass_fds
        observed_env = dict(kwargs["env"])
        observed_pass_fds = tuple(kwargs["pass_fds"])
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "pass",
        parent_death_signal=True,
    )

    assert result.returncode == 0
    assert result.runtime_authority is not None
    assert result.runtime_authority.execution_materialization_identity is not None
    assert observed_env[exec_guard_protocol.PARENT_PID_ENV] == str(os.getpid())
    execution_fd = int(observed_env[exec_guard_protocol.EXEC_FD_ENV])
    assert execution_fd in observed_pass_fds


@pytest.mark.parametrize("raw_parent", [None, "not-a-pid", "0", "-1"])
def test_exec_guard_parent_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    raw_parent: str | None,
) -> None:
    monkeypatch.setattr(exec_guard, "_ensure_owned_session", lambda: None)
    if raw_parent is None:
        monkeypatch.delenv(exec_guard_protocol.PARENT_PID_ENV, raising=False)
    else:
        monkeypatch.setenv(exec_guard_protocol.PARENT_PID_ENV, raw_parent)
    monkeypatch.setenv(exec_guard_protocol.EXEC_FD_ENV, "3")

    with pytest.raises(SystemExit, match="exec guard parent identity is (missing|invalid)"):
        exec_guard.main(["tool"])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sealed execution authority")
@pytest.mark.parametrize("raw_exec_fd", [None, "not-an-fd", "-1"])
def test_exec_guard_execution_descriptor_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    raw_exec_fd: str | None,
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
