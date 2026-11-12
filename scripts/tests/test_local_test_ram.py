from __future__ import annotations

import errno
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

import pytest

from oh_goon.core.process_birth import observe_process_birth
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandResult,
    CommandSupervisorReceipt,
)
from oh_goon.repository_tooling import local_test_ram as owner
from oh_goon.repository_tooling.frontend_test_results import (
    collect_native_vitest,
    require_native_vitest_files,
    vitest_outcomes,
)
from oh_goon.repository_tooling.pytest_resource_budget import (
    PytestCgroupObservation,
    PytestCgroupUsageSnapshot,
    PytestResourceBudget,
    resolve_pytest_resource_budget,
)

ROOT = Path(__file__).resolve().parents[2]


def _profile_receipt(stderr: str) -> dict[str, Any]:
    prefix = "RAM_TEST_PROFILE: "
    receipts = [
        json.loads(line.removeprefix(prefix))
        for line in stderr.splitlines()
        if line.startswith(prefix)
    ]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["cleanup"] == receipt["native_process_cleanup"] == "verified"
    assert receipt["cache"] in {"owned", "declined", "fallback"}
    return cast(dict[str, Any], receipt)


@pytest.fixture
def native_frontend_toolchain() -> None:
    """Declare native locked npm capability without setup during collection."""


def _native_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "NODE_COMPILE_CACHE",
        "NODE_DISABLE_COMPILE_CACHE",
        "OH_GOON_LOCAL_TEST_PROFILE",
    ):
        environment.pop(name, None)
    return {**environment, "OH_GOON_TEST_CPUS": "2"}


@pytest.fixture
def admitted(monkeypatch: pytest.MonkeyPatch) -> PytestResourceBudget:
    for name in (
        "NODE_COMPILE_CACHE",
        "NODE_DISABLE_COMPILE_CACHE",
        "OH_GOON_LOCAL_TEST_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    budget = resolve_pytest_resource_budget(2)
    budget = replace(
        budget,
        worker_ceiling=2,
        host_available_memory_bytes=16 * 1024**3,
        available_memory_bytes=16 * 1024**3,
        cgroup_cpu_capacity=2,
        cgroup_usage_started=PytestCgroupUsageSnapshot("test", True, None, None, None),
        cgroups=(
            PytestCgroupObservation(
                "test", "200000 100000", 2, None, 0, True, None, True
            ),
        ),
    )
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    return budget


@pytest.mark.parametrize(
    "setting", ["NODE_COMPILE_CACHE", "NODE_DISABLE_COMPILE_CACHE"]
)
def test_caller_native_settings_decline_allocation_unchanged(
    admitted: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    environment = {"PATH": os.defpath, setting: "caller-value"}
    calls = []
    monkeypatch.setattr(
        tempfile,
        "mkdtemp",
        lambda **kwargs: pytest.fail("caller setting allocated scratch"),
    )

    def run(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append((argv, kwargs["env"]))
        return CommandResult(7, "", "")

    monkeypatch.setattr(owner, "run_command", run)
    result = owner.run_profiled_command(
        ("native", "argument"), resolved_cpus=2, environment=environment
    )
    assert result.returncode == 7 and result.cache == "declined"
    assert calls == [(("native", "argument"), environment)]


@pytest.mark.parametrize(
    "unknown", ["low-memory", "host-memory", "cgroup-limit", "memory-high", "cpu"]
)
def test_native_resource_unknowns_refuse_or_decline_before_allocation(
    admitted: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch, unknown: str
) -> None:
    budget = admitted
    if unknown == "low-memory":
        budget = replace(budget, available_memory_bytes=owner.CACHE_BYTES)
    elif unknown == "host-memory":
        budget = replace(budget, host_available_memory_bytes=None)
    elif unknown == "cgroup-limit":
        budget = replace(
            budget, cgroups=(replace(budget.cgroups[0], memory_limit_observed=False),)
        )
    elif unknown == "memory-high":
        budget = replace(
            budget, cgroups=(replace(budget.cgroups[0], memory_high_bytes=1024),)
        )
    else:
        budget = replace(budget, cgroup_cpu_capacity=None)
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    monkeypatch.setattr(
        tempfile,
        "mkdtemp",
        lambda **kwargs: pytest.fail("invalid budget allocated scratch"),
    )
    calls = []

    def run(*args: str, **kwargs: Any) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "", "")

    monkeypatch.setattr(owner, "run_command", run)
    if unknown == "cpu":
        with pytest.raises(ValueError, match="CPU quota"):
            owner.run_profiled_command(("native",), resolved_cpus=2)
        assert calls == []
    else:
        assert (
            owner.run_profiled_command(("native",), resolved_cpus=2).cache == "declined"
        )
        assert calls == [("native",)]


@pytest.mark.parametrize("failure", ["unshare", "cancel-before-ready"])
def test_preflight_failure_falls_back_once_but_cancellation_never_retries(
    admitted: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls = []

    def run(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        if len(calls) == 1:
            if failure == "cancel-before-ready":
                raise CommandCancelledError(
                    "cancel", supervisor=CommandSupervisorReceipt()
                )
            return CommandResult(1, "", "unshare denied")
        return CommandResult(23, "", "")

    monkeypatch.setattr(owner, "run_command", run)
    result = owner.run_profiled_command(("native",), resolved_cpus=2)
    if failure == "unshare":
        assert len(calls) == 2 and calls[-1] == ("native",) and result.returncode == 23
    else:
        assert len(calls) == 1 and result.returncode < 0


@pytest.mark.parametrize("requested", [None, signal.SIGINT, signal.SIGTERM])
def test_prestart_cancellation_preserves_signal_without_allocating_or_executing(
    admitted: PytestResourceBudget,
    monkeypatch: pytest.MonkeyPatch,
    requested: int | None,
) -> None:
    monkeypatch.setattr(
        tempfile,
        "mkdtemp",
        lambda **kwargs: pytest.fail("cancelled command allocated scratch"),
    )
    monkeypatch.setattr(
        owner,
        "run_command",
        lambda *args, **kwargs: pytest.fail("cancelled command started work"),
    )
    result = owner.run_profiled_command(
        ("native",),
        resolved_cpus=2,
        cancel_requested=lambda: True,
        cancellation_signal=lambda: requested,
    )
    assert result.returncode == -(signal.SIGTERM if requested is None else requested)
    assert result.cache == "cancelled" and result.cleanup == "not-required"


@pytest.mark.parametrize(
    "fault", ["pipe-exhausted", "cache-directory-denied", "keeper-start-denied"]
)
def test_partial_cache_setup_failure_releases_resources_before_single_fallback(
    admitted: PytestResourceBudget,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
) -> None:
    roots: list[Path] = []
    descriptors: list[int] = []
    calls: list[tuple[str, ...]] = []
    observed_faults: list[str] = []
    native_mkdtemp = tempfile.mkdtemp
    native_pipe2 = os.pipe2
    native_mkdir = Path.mkdir

    def allocate(**kwargs: Any) -> str:
        root = native_mkdtemp(prefix=kwargs["prefix"], dir=tmp_path)
        roots.append(Path(root))
        return root

    def pipe2(flags: int) -> tuple[int, int]:
        if fault == "pipe-exhausted":
            observed_faults.append(fault)
            raise OSError(errno.EMFILE, "Too many open files")
        pair = native_pipe2(flags)
        descriptors.extend(pair)
        return pair

    def mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
        if fault == "cache-directory-denied" and roots and path == roots[0] / "cache":
            observed_faults.append(fault)
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        native_mkdir(path, *args, **kwargs)

    def run(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        assert argv == ("native", "literal argument")
        assert kwargs["env"] == {"PATH": os.defpath}
        return CommandResult(23, "", "")

    def start_denied(self: Thread) -> None:
        observed_faults.append("keeper-start-denied")
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(tempfile, "mkdtemp", allocate)
    monkeypatch.setattr(os, "pipe2", pipe2)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(owner, "run_command", run)
    if fault == "keeper-start-denied":
        monkeypatch.setattr(Thread, "start", start_denied)
    try:
        result = owner.run_profiled_command(
            ("native", "literal argument"),
            resolved_cpus=2,
            environment={"PATH": os.defpath},
        )
        assert result.returncode == 23 and result.cache == "fallback"
        assert result.cleanup == result.native_process_cleanup == "verified"
        assert observed_faults == [fault]
        assert calls == [("native", "literal argument")]
        assert roots and not any(root.exists() for root in roots)
        for descriptor in descriptors:
            with pytest.raises(OSError) as closed:
                os.fstat(descriptor)
            assert closed.value.errno == errno.EBADF
    finally:
        # Failed implementations must not leave the regression's resources behind.
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
        for root in roots:
            if (root / "cache").exists():
                (root / "cache").rmdir()
            if root.exists():
                root.rmdir()


def _ready_keeper(
    monkeypatch: pytest.MonkeyPatch,
    *,
    original_quiescent: bool = True,
    access: bool = True,
    cleanup_failure: bool = False,
) -> tuple[list[tuple[str, ...]], list[Path]]:
    calls: list[tuple[str, ...]] = []
    roots: list[Path] = []
    native_capacity = os.statvfs("/tmp")

    def capacity(_descriptor: int) -> os.statvfs_result:
        if not access:
            raise PermissionError("cache access denied")
        fields = list(native_capacity)
        fields[2] = owner.CACHE_BYTES // native_capacity.f_frsize
        return os.statvfs_result(fields)

    monkeypatch.setattr(os, "fstatvfs", capacity)
    if cleanup_failure:
        monkeypatch.setattr(
            Path,
            "rmdir",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("busy")),
        )

    def run(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        if argv[0] == "native":
            return CommandResult(
                17,
                "",
                "",
                supervisor=CommandSupervisorReceipt(
                    final_process_group_quiescent=original_quiescent
                ),
            )
        root = Path(argv[argv.index("--namespace-keeper") + 1])
        roots.append(root)
        birth = observe_process_birth(os.getpid())
        assert birth is not None
        kwargs["process_observer"](
            CommandProcessLifecycleEvent(
                "PROCESS_STARTED",
                "fixture",
                os.getpid(),
                f"{birth.boot_id}:{birth.start_ticks}",
            )
        )
        os.write(kwargs["pass_fds"][0], json.dumps({"pid": os.getpid()}).encode())
        deadline = time.monotonic() + 3
        while not kwargs["cancel_requested"]() and time.monotonic() < deadline:
            time.sleep(0.005)
        return CommandResult(0, "", "")

    monkeypatch.setattr(owner, "run_command", run)
    return calls, roots


def test_late_preflight_error_releases_cache_before_original_execution(
    admitted: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, roots = _ready_keeper(monkeypatch)
    original = cast(Callable[..., CommandResult], vars(owner)["run_command"])
    observations: list[str] = []

    def access(_path: Path) -> bool:
        observations.append("access-error")
        raise OSError(errno.EIO, "cache probe failed")

    def run(*argv: str, **kwargs: Any) -> CommandResult:
        if argv[0] == "native":
            assert "NODE_COMPILE_CACHE" not in kwargs["env"]
            assert roots and not any(root.exists() for root in roots)
        return original(*argv, **kwargs)

    monkeypatch.setattr(owner, "_cache_access", access)
    monkeypatch.setattr(owner, "run_command", run)
    result = owner.run_profiled_command(("native",), resolved_cpus=2)
    assert result.returncode == 17 and result.cache == "fallback"
    assert result.cleanup == result.native_process_cleanup == "verified"
    assert observations == ["access-error"]
    assert sum(argv[0] == "native" for argv in calls) == 1


@pytest.mark.parametrize("fault", ["access", "process-cleanup", "file-cleanup"])
def test_ready_cache_failure_preserves_native_result_and_converges(
    admitted: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    calls, roots = _ready_keeper(
        monkeypatch,
        original_quiescent=fault != "process-cleanup",
        access=fault != "access",
        cleanup_failure=fault == "file-cleanup",
    )
    started = time.monotonic()
    result = owner.run_profiled_command(("native",), resolved_cpus=2)
    assert time.monotonic() - started < 1.5
    assert result.returncode == 17 and sum(argv[0] == "native" for argv in calls) == 1
    if fault == "access":
        assert result.cache == "fallback" and result.cleanup == "verified"
    else:
        assert result.cleanup == "incomplete" and roots[0].is_dir()
        assert result.native_process_cleanup == (
            "incomplete" if fault == "process-cleanup" else "verified"
        )
        # The fixture has no live processes; release retained test scratch.
        subprocess.run(["rm", "-rf", str(roots[0])], check=True)


@pytest.mark.process
@pytest.mark.native_resources
def test_optional_cache_preserves_workload_identity_and_native_settings(
    tmp_path: Path, record_property: Callable[[str, object], None]
) -> None:
    script = """
import json, os, pathlib, shutil, subprocess, sys
cache_text=os.environ.get('NODE_COMPILE_CACHE')
facts={}
node_qualification='NOT_RUN'
os.write(int(sys.argv[2]), b'native-fd')
if cache_text:
 cache=pathlib.Path(cache_text)
 info=os.statvfs(cache)
 original=pathlib.Path(str(cache).partition('/root')[2])
 facts=dict(capacity=info.f_blocks*info.f_frsize,original_empty=not list(original.iterdir()))
 node=shutil.which('node')
 if node:
  module=pathlib.Path(sys.argv[1]).with_suffix('.cjs')
  module.write_text('module.exports = 42;')
  code='require('+json.dumps(str(module))+'); const m=require("node:module"); console.log(JSON.stringify(typeof m.getCompileCacheDir === "function" ? m.getCompileCacheDir() : null))'
  native=subprocess.run([node,'-e',code],check=True,capture_output=True,text=True)
  selected=json.loads(native.stdout)
  if selected is not None:
   assert pathlib.Path(selected).is_relative_to(pathlib.Path(cache_text)) and any(path.is_file() for path in cache.rglob('*'))
   node_qualification='PASS'
pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(uid=os.getuid(),gid=os.getgid(),groups=os.getgroups(),user=os.readlink('/proc/self/ns/user'),mount=os.readlink('/proc/self/ns/mnt'),stdin=sys.stdin.read(),cache=cache_text,node_qualification=node_qualification,**facts)))
raise SystemExit(19)
"""
    for mode in ("optional", "caller-disabled"):
        output = tmp_path / f"{mode}.json"
        read_fd, write_fd = os.pipe()
        environment = _native_environment()
        if mode == "caller-disabled":
            environment["NODE_DISABLE_COMPILE_CACHE"] = "1"
        try:
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/development_test_resources.sh"),
                    str(ROOT / "backend/.venv/bin/oh-goon-local-test-ram"),
                    "--resolved-cpus",
                    "2",
                    "--",
                    sys.executable,
                    "-c",
                    script,
                    str(output),
                    str(write_fd),
                ],
                env=environment,
                input="native-stdin",
                text=True,
                capture_output=True,
                pass_fds=(write_fd,),
                timeout=30,
            )
            assert result.returncode == 19, result.stderr
            observed = json.loads(output.read_text())
            assert {
                name: observed[name]
                for name in ("uid", "gid", "groups", "user", "mount", "stdin")
            } == dict(
                uid=os.getuid(),
                gid=os.getgid(),
                groups=os.getgroups(),
                user=os.readlink("/proc/self/ns/user"),
                mount=os.readlink("/proc/self/ns/mnt"),
                stdin="native-stdin",
            )
            assert os.read(read_fd, 9) == b"native-fd"
            receipt = _profile_receipt(result.stderr)
            assert receipt["native_exit_code"] == 19
            owned = observed["cache"] is not None
            assert owned == (receipt["cache"] == "owned")
            if owned:
                assert observed["capacity"] == owner.CACHE_BYTES
                assert observed["original_empty"] is True
            else:
                assert receipt["cache"] in {"declined", "fallback"}
                assert observed["node_qualification"] == "NOT_RUN"
            if mode == "caller-disabled":
                assert not owned and receipt["cache"] == "declined"
            record_property(f"ram_mount_{mode}", "PASS" if owned else "NOT_RUN")
            record_property(
                f"node_compile_cache_{mode}", observed["node_qualification"]
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)


@pytest.mark.process
@pytest.mark.native_resources
@pytest.mark.parametrize("cache_disabled", [False, True])
def test_ram_profile_preserves_native_configuration_and_read_only_path_resolution(
    tmp_path: Path, cache_disabled: bool
) -> None:
    script = """
import json, os
from oh_goon.runtime.database import default_database_path
from oh_goon.runtime.league_folders import default_league_preferences_path
names=('OH_GOON_DB','OH_GOON_LEAGUE_FILE','OH_GOON_SQLITE_JOURNAL_MODE','OH_GOON_PROGRESS_FD')
selected={name:os.environ.get(name) for name in names}
print(json.dumps(dict(environment=selected,database=str(default_database_path()),league=str(default_league_preferences_path()))))
if selected['OH_GOON_PROGRESS_FD'] is not None:
 os.write(int(selected['OH_GOON_PROGRESS_FD']),b'native-progress')
"""
    for mode in ("baseline", "ram"):
        read_fd, write_fd = os.pipe()
        selected = {
            "OH_GOON_DB": "state/../configured/development.sqlite",
            "OH_GOON_LEAGUE_FILE": "preferences/../configured/league.yml",
            "OH_GOON_SQLITE_JOURNAL_MODE": "DELETE",
            "OH_GOON_PROGRESS_FD": str(write_fd),
        }
        environment = (
            _native_environment()
            | selected
            | {
                "XDG_DATA_HOME": str(tmp_path / "unselected-data"),
                "XDG_CONFIG_HOME": str(tmp_path / "unselected-config"),
            }
        )
        command = [sys.executable, "-c", script]
        if cache_disabled:
            environment["NODE_DISABLE_COMPILE_CACHE"] = "1"
        if mode == "ram":
            environment["OH_GOON_LOCAL_TEST_PROFILE"] = "ram"
            if cache_disabled:
                # Exercise the receiving owner's declined branch even though the
                # shell normally reuses caller-native cache settings by exec.
                command = [
                    str(ROOT / "backend/.venv/bin/oh-goon-local-test-ram"),
                    "--resolved-cpus",
                    "2",
                    "--",
                    *command,
                ]
        try:
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/development_test_resources.sh"), *command],
                cwd=tmp_path,
                env=environment,
                pass_fds=(write_fd,),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {
                "environment": selected,
                "database": str(tmp_path / "configured/development.sqlite"),
                "league": str(tmp_path / "configured/league.yml"),
            }
            assert select.select([read_fd], [], [], 1)[0]
            assert os.read(read_fd, 15) == b"native-progress"
            if mode == "ram":
                receipt = _profile_receipt(result.stderr)
                assert receipt["native_exit_code"] == 0
                if cache_disabled:
                    assert receipt["cache"] == "declined"
            assert not list(tmp_path.iterdir()), (
                "path inspection created application state"
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)


@pytest.mark.process
@pytest.mark.native_resources
def test_optional_cache_preserves_native_failure_and_checks_enospc_when_admitted(
    tmp_path: Path, record_property: Callable[[str, object], None]
) -> None:
    count = tmp_path / "count"
    script = r"""
import errno, json, os, pathlib, sys
with pathlib.Path(sys.argv[1]).open('a') as count: count.write('once\n')
cache=os.environ.get('NODE_COMPILE_CACHE')
if cache:
 try:
  with open(pathlib.Path(cache)/'full', 'wb', buffering=0) as output:
   os.posix_fallocate(output.fileno(), 0, 600*1024*1024)
 except OSError as error:
  assert error.errno == errno.ENOSPC
  print(json.dumps(dict(cache=cache,enospc_qualification='PASS')))
  raise SystemExit(27)
 raise SystemExit(3)
print(json.dumps(dict(cache=None,enospc_qualification='NOT_RUN')))
raise SystemExit(27)
"""
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/development_test_resources.sh"),
            str(ROOT / "backend/.venv/bin/oh-goon-local-test-ram"),
            "--resolved-cpus",
            "2",
            "--",
            sys.executable,
            "-c",
            script,
            str(count),
        ],
        env=_native_environment(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 27, result.stderr
    assert count.read_text() == "once\n"
    observed = json.loads(result.stdout)
    receipt = _profile_receipt(result.stderr)
    assert receipt["native_exit_code"] == 27
    owned = observed["cache"] is not None
    assert owned == (receipt["cache"] == "owned")
    assert observed["enospc_qualification"] == ("PASS" if owned else "NOT_RUN")
    if not owned:
        assert receipt["cache"] in {"declined", "fallback"}
    record_property("ram_enospc_qualification", observed["enospc_qualification"])


def test_keeper_loss_during_work_aborts_without_reexecution(
    admitted: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _roots = _ready_keeper(monkeypatch)
    original = cast(Callable[..., CommandResult], vars(owner)["run_command"])
    started = Event()

    def run(*argv: str, **kwargs: Any) -> CommandResult:
        if argv[0] == "native":
            calls.append(argv)
            started.set()
            deadline = time.monotonic() + 3
            while not kwargs["cancel_requested"]() and time.monotonic() < deadline:
                time.sleep(0.005)
            assert kwargs["cancel_requested"]()
            raise CommandCancelledError(
                "keeper lost", supervisor=CommandSupervisorReceipt()
            )
        callbacks = dict(kwargs)
        callbacks["cancel_requested"] = started.is_set
        return original(*argv, **callbacks)

    monkeypatch.setattr(owner, "run_command", run)
    result = owner.run_profiled_command(("native",), resolved_cpus=2)
    assert result.returncode < 0 and result.cache == "lost"
    assert sum(argv[0] == "native" for argv in calls) == 1


@pytest.mark.process
@pytest.mark.native_resources
def test_optional_cache_sigterm_settles_original_and_admitted_keeper(
    tmp_path: Path, record_property: Callable[[str, object], None]
) -> None:
    marker = tmp_path / "started.json"
    script = "import json,os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'cache':os.environ.get('NODE_COMPILE_CACHE')})); time.sleep(60)"
    process = subprocess.Popen(
        [
            "bash",
            str(ROOT / "scripts/development_test_resources.sh"),
            str(ROOT / "backend/.venv/bin/oh-goon-local-test-ram"),
            "--resolved-cpus",
            "2",
            "--",
            sys.executable,
            "-c",
            script,
            str(marker),
        ],
        env=_native_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    descriptors = []
    try:
        deadline = time.monotonic() + 10
        while (
            not marker.is_file()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert marker.is_file(), process.communicate(timeout=5)
        started = json.loads(marker.read_text())
        descriptors.append(os.pidfd_open(started["pid"]))
        owned = started["cache"] is not None
        if owned:
            descriptors.append(os.pidfd_open(int(Path(started["cache"]).parts[2])))
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=15)
        assert process.returncode in {-signal.SIGTERM, 128 + signal.SIGTERM}, stderr
        receipt = _profile_receipt(stderr)
        assert owned == (receipt["cache"] == "owned")
        if not owned:
            assert receipt["cache"] in {"declined", "fallback"}
        for descriptor in descriptors:
            poll = select.poll()
            poll.register(descriptor, select.POLLIN)
            assert poll.poll(1000), "owned native process survived cancellation"
        record_property(
            "ram_keeper_cancellation_qualification", "PASS" if owned else "NOT_RUN"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        for descriptor in descriptors:
            os.close(descriptor)


@pytest.mark.process
@pytest.mark.native_resources
@pytest.mark.parametrize("cache_disabled", [False, True])
def test_ram_profile_delivers_sigint_to_native_handler_and_waits_for_cleanup(
    tmp_path: Path, cache_disabled: bool
) -> None:
    script = """
import json, os, pathlib, signal, sys
ready,closed=map(pathlib.Path,sys.argv[1:])
received=[]
def interrupt(signum,frame):
 received.append(signum)
 raise SystemExit(128+signum)
signal.signal(signal.SIGINT,interrupt)
signal.signal(signal.SIGTERM,interrupt)
try:
 pending=ready.with_suffix('.partial')
 pending.write_text(json.dumps(dict(pid=os.getpid(),cache=os.environ.get('NODE_COMPILE_CACHE'))))
 pending.replace(ready)
 while True: signal.pause()
finally:
 cache=os.environ.get('NODE_COMPILE_CACHE')
 closed.write_text(json.dumps(dict(received=received,cleanup=True,cache_accessible=cache is None or pathlib.Path(cache).is_dir())))
"""
    for mode in ("baseline", "ram"):
        marker = tmp_path / f"{mode}-started.json"
        closed = tmp_path / f"{mode}-closed.json"
        environment = _native_environment()
        command = [sys.executable, "-c", script, str(marker), str(closed)]
        if cache_disabled:
            environment["NODE_DISABLE_COMPILE_CACHE"] = "1"
        if mode == "ram":
            environment["OH_GOON_LOCAL_TEST_PROFILE"] = "ram"
            if cache_disabled:
                command = [
                    str(ROOT / "backend/.venv/bin/oh-goon-local-test-ram"),
                    "--resolved-cpus",
                    "2",
                    "--",
                    *command,
                ]
        process = subprocess.Popen(
            ["bash", str(ROOT / "scripts/development_test_resources.sh"), *command],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        descriptors: list[int] = []
        try:
            deadline = time.monotonic() + 10
            while (
                not marker.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert marker.exists(), process.communicate(timeout=5)
            started = json.loads(marker.read_text())
            descriptors.append(os.pidfd_open(started["pid"]))
            if started["cache"] is not None:
                descriptors.append(os.pidfd_open(int(Path(started["cache"]).parts[2])))
            os.killpg(process.pid, signal.SIGINT)
            _stdout, stderr = process.communicate(timeout=15)
            assert process.returncode in {-signal.SIGINT, 128 + signal.SIGINT}, stderr
            assert json.loads(closed.read_text()) == {
                "received": [signal.SIGINT],
                "cleanup": True,
                "cache_accessible": True,
            }
            if mode == "ram":
                receipt = _profile_receipt(stderr)
                assert receipt["native_exit_code"] in {
                    -signal.SIGINT,
                    128 + signal.SIGINT,
                }
                assert (started["cache"] is not None) == (receipt["cache"] == "owned")
                if cache_disabled:
                    assert receipt["cache"] == "declined"
            for descriptor in descriptors:
                poll = select.poll()
                poll.register(descriptor, select.POLLIN)
                assert poll.poll(1000), (
                    "owned native process survived interrupt cleanup"
                )
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)
            for descriptor in descriptors:
                os.close(descriptor)


@pytest.mark.process
@pytest.mark.native_resources
def test_authored_node_and_dom_membership_is_identical_with_ram_profile(
    tmp_path: Path,
    native_frontend_toolchain: None,
    record_property: Callable[[str, object], None],
) -> None:
    files = (
        "src/domain/concurrency/MutationController.test.ts",
        "src/domain/reads/readResources.test.ts",
        "src/app/pages/runStepUrl.test.ts",
        "src/app/pages/CatalogPage.test.tsx",
        "src/features/game-tape/components/GameTapeOutputs.test.tsx",
    )
    targets = tuple("frontend/" + path for path in files)
    collection = collect_native_vitest(ROOT, targets)
    projects = {row["file"]: row["projectName"] for row in collection["selected"]}
    assert set(projects.values()) == {"node", "dom"}
    signatures = []
    counts = []
    for mode in ("baseline", "ram"):
        directory = tmp_path / mode
        directory.mkdir(mode=0o700)
        report = directory / "vitest.json"
        environment = _native_environment()
        environment["OH_GOON_VITEST_JSON_REPORT"] = str(report)
        startup = directory / "native-node-startup.jsonl"
        preload = directory / "observe-native-cache.mjs"
        preload.write_text(
            'import {appendFileSync} from "node:fs";import * as m from "node:module";import {isMainThread} from "node:worker_threads";\n'
            + "appendFileSync("
            + json.dumps(str(startup))
            + ',JSON.stringify({main:isMainThread,env:process.env.NODE_COMPILE_CACHE??null,available:typeof m.getCompileCacheDir==="function",cache:typeof m.getCompileCacheDir==="function"?m.getCompileCacheDir()??null:null})+"\\n");\n'
        )
        environment["NODE_OPTIONS"] = (
            environment.get("NODE_OPTIONS", "") + " --import=" + preload.as_uri()
        ).strip()
        if mode == "ram":
            environment.pop("OH_GOON_TEST_CPUS")
            environment["OH_GOON_LOCAL_TEST_PROFILE"] = "ram"
        native = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts/development_test_resources.sh"),
                "sh",
                "-c",
                'cd "$1" && shift && exec "$@"',
                "sh",
                str(ROOT / "frontend"),
                "npm",
                "run",
                "test",
                "--",
                *("./" + path for path in files),
            ],
            env=environment,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert native.returncode == 0, (native.stdout, native.stderr)
        assert "at most 2 CPUs" in native.stderr
        observations = [json.loads(line) for line in startup.read_text().splitlines()]
        native_main = [row for row in observations if row["main"]]
        assert native_main
        if mode == "ram":
            receipt = _profile_receipt(native.stderr)
            assert receipt["native_exit_code"] == 0
            owned = receipt["cache"] == "owned"
            assert all((row["env"] is not None) == owned for row in native_main)
            if owned:
                assert all(
                    not row["available"]
                    or (
                        isinstance(row["cache"], str)
                        and Path(row["cache"]).is_relative_to(Path(row["env"]))
                    )
                    for row in native_main
                )
            else:
                assert receipt["cache"] in {"declined", "fallback"}
            qualified = owned and all(row["available"] for row in native_main)
            record_property(
                "authored_vitest_ram_compile_cache_qualification",
                "PASS" if qualified else "NOT_RUN",
            )
        else:
            assert all(row["env"] is None for row in native_main)
        with report.open("rb") as source:
            encoded = source.read(4 * 1024 * 1024 + 1)
        assert len(encoded) <= 4 * 1024 * 1024
        payload = json.loads(encoded)
        complete, passed, native_counts = vitest_outcomes(payload)
        assert complete and passed and native_counts["numPassedTests"] > 0
        assert require_native_vitest_files(
            payload,
            project_root=ROOT / "frontend",
            expected_files=targets,
            collection=collection,
        ) == set(targets)
        cases = []
        project_counts = {"node": 0, "dom": 0}
        for result in payload["testResults"]:
            path = (
                "frontend/"
                + Path(result["name"]).relative_to(ROOT / "frontend").as_posix()
            )
            for assertion in result["assertionResults"]:
                cases.append((path, assertion["fullName"], assertion["status"]))
                project_counts[projects[path]] += 1
        assert all(value > 0 for value in project_counts.values())
        signatures.append(sorted(cases))
        counts.append(native_counts)
    assert signatures[0] == signatures[1]
    assert counts[0] == counts[1]
