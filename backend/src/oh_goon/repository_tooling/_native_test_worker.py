"""Explicit native test-worker preparation behind ci_native_runtime.sh."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import FrameType
from typing import Any, Literal, TypedDict
from uuid import uuid4

from pydantic import Field, field_validator

from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.base import ImmutableModel
from oh_goon.engine.command import CommandCancelledError, run_command
from oh_goon.integrations import _fleet_plan_cgroup_resources as cgroups
from oh_goon.integrations.exact_file_write import publish_text_file

_PROFILE: Literal["shared-user-manager"] = "shared-user-manager"
_MAX_PACKET = 64 * 1024
_MANAGER_ENV = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
_CONTROLS = ("cpu.max", *cgroups._STANDARD_MEMORY_CONTROLS, "pids.max")


class _SharedAncestor(TypedDict):
    path: str
    controls: dict[str, str]


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def _cancellation() -> Iterator[tuple[Event, list[int]]]:
    requested = Event()
    received: list[int] = []

    def request(signum: int, _frame: FrameType | None) -> None:
        received.append(signum)
        requested.set()

    previous = {
        signum: signal.signal(signum, request) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield requested, received
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class _WorkerAuthority(ImmutableModel):
    profile: Literal["shared-user-manager"]
    unit: str = Field(pattern=r"^oh-goon-native-worker-[0-9a-f]{32}\.scope$")
    root: Path
    python: Path
    systemd_run: Path
    systemd_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    systemctl: Path
    systemctl_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manager_environment: tuple[tuple[str, str], ...]
    cpus: int = Field(ge=0, le=4096)
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    caller_source_revision: str | None
    report: Path | None = None

    @field_validator("root", "python", "systemd_run", "systemctl", "report")
    @classmethod
    def absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and (not value.is_absolute() or ".." in value.parts):
            raise ValueError("Native worker paths must be absolute")
        return value

    @field_validator("manager_environment")
    @classmethod
    def manager_names(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        names = [name for name, _setting in value]
        if len(names) != len(set(names)) or any(name not in _MANAGER_ENV for name in names):
            raise ValueError("Native worker manager environment changed its selected authority")
        return value


def _read_packet(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError("Native worker packet must be an owned private regular file")
        payload = stream.read(_MAX_PACKET + 1)
    if len(payload) > _MAX_PACKET:
        raise ValueError("Native worker packet exceeds its bound")
    packet = json.loads(payload)
    if not isinstance(packet, dict):
        raise ValueError("Native worker packet must be an object")
    return packet


def _query(authority: _WorkerAuthority) -> dict[str, str]:
    if _digest(authority.systemctl) != authority.systemctl_sha256:
        raise ValueError("Selected native systemctl changed")
    result = run_command(
        str(authority.systemctl),
        "--user",
        "--no-pager",
        "show",
        authority.unit,
        "--property=InvocationID,ControlGroup,Slice,ActiveState,LoadState",
        env=dict(authority.manager_environment),
        timeout=5,
        max_capture_bytes_per_stream=8192,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or result.stdout_truncated or "LoadState" not in properties:
        raise ValueError("Native worker unit observation is unavailable")
    return properties


def _enter(authority_file: Path, acknowledgement: Path, command: Sequence[str]) -> None:
    authority = _WorkerAuthority.model_validate(_read_packet(authority_file))
    if dict(authority.manager_environment) != {
        name: os.environ[name] for name in _MANAGER_ENV if name in os.environ
    }:
        raise ValueError("Native worker changed its selected manager environment")
    if Path(__file__).resolve().parents[4] != authority.root:
        raise ValueError("Native worker imported a different checkout")
    if Path(sys.executable).absolute() != authority.python:
        raise ValueError("Native worker changed its selected interpreter")
    if _digest(authority.systemd_run) != authority.systemd_run_sha256:
        raise ValueError("Selected native systemd-run changed")
    if (
        _digest(authority.root / "backend/pyproject.toml") != authority.project_sha256
        or _digest(authority.root / "backend/uv.lock") != authority.lock_sha256
    ):
        raise ValueError("Native worker project environment authority changed")
    properties = _query(authority)
    invocation = properties.get("InvocationID", "")
    if not re.fullmatch(r"[0-9a-f]{32}", invocation):
        raise ValueError("Native worker invocation identity is unavailable")
    publish_text_file(
        acknowledgement,
        json.dumps({"unit": authority.unit, "invocation_id": invocation}) + "\n",
    )
    mountinfo = cgroups._standard_mountinfo_snapshot()
    root = Path("/sys/fs/cgroup")
    caller = cgroups._standard_cgroup_directory(root, mountinfo=mountinfo)
    if (
        caller.name != authority.unit
        or properties.get("ControlGroup") != "/" + caller.relative_to(root).as_posix()
        or properties.get("Slice") != cgroups.STANDARD_RESOURCE_SLICE
        or caller.parent.name != cgroups.STANDARD_RESOURCE_SLICE
    ):
        raise ValueError("Native worker differs from its selected unit and shared slice")
    with cgroups._bound_cgroup_directory(caller, cgroups._exact_cgroup_identity(caller)) as handle:
        cgroups._require_standard_domain(handle)
        cgroups._require_standard_caller_membership(handle)
        cgroups._require_unlimited_private_controls(handle)
    shared: list[_SharedAncestor] = []
    current = caller.parent
    while True:
        controls = {}
        with cgroups._bound_cgroup_directory(
            current, cgroups._exact_cgroup_identity(current)
        ) as fd:
            for name in _CONTROLS:
                try:
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                controls[name] = cgroups._read_bound_cgroup_control(fd, name, 128).strip()
        shared.append({"path": str(current), "controls": controls})
        if current == root:
            break
        current = current.parent
    context = {
        "profile": authority.profile,
        "scope": "new explicit qualification worker; original caller is not claimed qualified",
        "unit": authority.unit,
        "invocation_id": invocation,
        "cgroup": str(caller),
        "shared_ancestors": shared,
        "python": str(authority.python),
        "python_version": sys.version,
        "root": str(authority.root),
        "caller_source_revision": authority.caller_source_revision,
        "project_sha256": authority.project_sha256,
        "lock_sha256": authority.lock_sha256,
        "configured_cpus": authority.cpus,
        "systemd_run": str(authority.systemd_run),
        "systemd_run_sha256": authority.systemd_run_sha256,
        "systemctl": str(authority.systemctl),
        "systemctl_sha256": authority.systemctl_sha256,
    }
    if authority.report is not None:
        publish_text_file(authority.report, json.dumps(context, sort_keys=True) + "\n")
    print("NATIVE QUALIFICATION WORKER: " + json.dumps(context, sort_keys=True), file=sys.stderr)
    selected = tuple(command)
    if authority.cpus:
        # This existing receiving-owner mode verifies an inherited shared quota
        # and refuses without creating a private scope or changing the ceiling.
        selected = (
            "/bin/bash",
            str(authority.root / "scripts/development_test_resources.sh"),
            "--oh-goon-cgroup-child",
            str(authority.cpus),
            "--",
            *selected,
        )
    os.execvpe(selected[0], selected, os.environ)


def _cleanup(authority: _WorkerAuthority, acknowledgement: Path) -> None:
    if not acknowledgement.exists():
        return  # A colliding/preexisting unit never executed our bootstrap.
    acknowledgement_data = _read_packet(acknowledgement)
    current = _query(authority)
    if current.get("LoadState") == "not-found":
        return
    if acknowledgement_data != {
        "unit": authority.unit,
        "invocation_id": current.get("InvocationID"),
    }:
        raise ValueError("Native worker cleanup invocation changed")
    result = run_command(
        str(authority.systemctl),
        "--user",
        "stop",
        authority.unit,
        env=dict(authority.manager_environment),
        timeout=30,
        max_capture_bytes_per_stream=8192,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    final = _query(authority)
    if result.returncode or not (
        final.get("LoadState") == "not-found"
        or (final.get("ActiveState") in {"inactive", "failed"} and not final.get("ControlGroup"))
    ):
        raise ValueError("Native worker cleanup did not converge")


def launch(root: Path, cpus: int, report: Path | None, command: Sequence[str]) -> int:
    if not command:
        raise ValueError("Native worker requires the original command")
    if sys.platform != "linux":
        raise ValueError("The selected native qualification profile requires Linux systemd")
    if root != Path(__file__).resolve().parents[4]:
        raise ValueError("Native worker owner differs from the selected checkout")
    if report is not None and (
        not report.is_absolute()
        or report.is_relative_to(root)
        or report.parent.resolve(strict=True) != report.parent
        or report.parent.stat().st_mode & 0o077
    ):
        raise ValueError("Native worker report requires private scratch outside source")
    selected = tuple(shutil.which(name) for name in ("systemd-run", "systemctl"))
    if any(path is None for path in selected):
        raise ValueError("Native qualification requires systemd-run and systemctl")
    runner, query = (Path(str(path)).resolve(strict=True) for path in selected)
    authority = _WorkerAuthority(
        profile=_PROFILE,
        unit=f"oh-goon-native-worker-{uuid4().hex}.scope",
        root=root,
        python=Path(sys.executable).absolute(),
        systemd_run=runner,
        systemd_run_sha256=_digest(runner),
        systemctl=query,
        systemctl_sha256=_digest(query),
        manager_environment=tuple(
            (name, os.environ[name]) for name in _MANAGER_ENV if name in os.environ
        ),
        cpus=cpus,
        project_sha256=_digest(root / "backend/pyproject.toml"),
        lock_sha256=_digest(root / "backend/uv.lock"),
        caller_source_revision=os.environ.get("FROZEN_SHA") or os.environ.get("GITHUB_SHA"),
        report=report,
    )
    scratch_parent = report.parent if report is not None else Path(tempfile.gettempdir()).resolve()
    if scratch_parent.is_relative_to(root):
        raise ValueError("Native worker scratch must remain outside source")
    primary: BaseException | None = None
    status: int | None = None
    with (
        tempfile.TemporaryDirectory(prefix="oh-goon-native-worker-", dir=scratch_parent) as scratch,
        _cancellation() as (cancelled, received),
    ):
        packet, acknowledgement = Path(scratch) / "authority.json", Path(scratch) / "worker.json"
        publish_text_file(packet, authority.model_dump_json() + "\n")
        stop_requested = False

        def cancellation_requested() -> bool:
            nonlocal stop_requested
            if cancelled.is_set() and not stop_requested:
                stop_requested = True
                # The scope also owns detached children holding output pipes.
                # Stop it before the command owner waits for pipe/group closure.
                _cleanup(authority, acknowledgement)
            return cancelled.is_set()

        try:
            result = run_command(
                str(runner),
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                "--expand-environment=no",
                "--slice=" + cgroups.STANDARD_RESOURCE_SLICE,
                "--unit=" + authority.unit,
                "--description=Oh-Goon explicit native test qualification",
                "--property=CPUWeight=100",
                "--property=MemoryAccounting=yes",
                "--property=TasksAccounting=yes",
                "--property=TasksMax=infinity",
                "--property=TimeoutStopSec=5s",
                "--",
                str(authority.python),
                "-I",
                "-m",
                "oh_goon.repository_tooling._native_test_worker",
                "--enter",
                str(packet),
                "--acknowledgement",
                str(acknowledgement),
                "--",
                *command,
                cwd=root,
                timeout=None,
                cancel_requested=cancellation_requested,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                max_capture_bytes_per_stream=64 * 1024,
                retain_output_tail=True,
                stream_output=lambda stream, text: print(
                    text, end="", file=sys.stdout if stream == "stdout" else sys.stderr, flush=True
                ),
            )
            status = 128 + received[-1] if received else result.returncode
            if status == 0 and not acknowledgement.exists():
                raise ValueError("Native worker did not acknowledge its invocation")
        except CommandCancelledError as error:
            if not received:
                primary = error
                raise
            primary = error
            status = 128 + received[-1]
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                _cleanup(authority, acknowledgement)
            except BaseException as error:
                if primary is not None:
                    primary.add_note(f"Native qualification cleanup failed: {error}")
                    if status is not None:
                        print(f"Native qualification cleanup failed: {error}", file=sys.stderr)
                elif status:
                    print(f"Native qualification cleanup failed: {error}", file=sys.stderr)
                else:
                    raise
    assert status is not None
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--cpus", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--enter", type=Path)
    parser.add_argument("--acknowledgement", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        if args.enter is not None:
            if args.acknowledgement is None or not command:
                raise ValueError("Native worker re-entry is incomplete")
            _enter(args.enter, args.acknowledgement, command)
            return 2
        if args.root is None or args.cpus is None:
            raise ValueError("Native worker checkout and resolved CPU selection are required")
        return launch(args.root, args.cpus, args.report, command)
    except (FleetDomainError, OSError, ValueError) as error:
        print(f"Native qualification unavailable: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
