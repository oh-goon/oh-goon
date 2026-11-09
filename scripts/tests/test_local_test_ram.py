from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
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


@pytest.fixture(scope="module")
def native_ram_capability(tmp_path_factory: pytest.TempPathFactory) -> None:
    budget = resolve_pytest_resource_budget(2)
    needed = owner.HEADROOM_BYTES + 2 * 512 * 1024**2 + owner.CACHE_BYTES
    if (
        budget.host_available_memory_bytes is None
        or budget.available_memory_bytes is None
        or budget.available_memory_bytes < needed
        or not budget.cgroups
        or any(
            not row.memory_limit_observed
            or not row.memory_high_observed
            or (
                row.memory_high_bytes is not None
                and (
                    row.memory_current_bytes is None
                    or row.memory_high_bytes - row.memory_current_bytes < needed
                )
            )
            for row in budget.cgroups
        )
    ):
        pytest.skip(
            "native optional RAM headroom unavailable; cache qualification not run"
        )
    if sys.platform != "linux" or any(
        shutil.which(name) is None
        for name in ("unshare", "mount", "node", "systemd-run")
    ):
        pytest.skip("native Linux user namespace, Node and CPU scope tools unavailable")
    node = subprocess.run(
        ["node", "-p", "typeof require('node:module').getCompileCacheDir"],
        env=_native_environment(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if node.returncode or node.stdout.strip() != "function":
        pytest.skip("native Node compile cache API unavailable; qualification not run")
    directory = tmp_path_factory.mktemp("native-ram-capability")
    probe = subprocess.run(
        [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--propagation",
            "private",
            "--",
            "mount",
            "-t",
            "tmpfs",
            "-o",
            "size=4096,noexec,nodev,nosuid,mode=0700",
            "tmpfs",
            str(directory),
        ],
        capture_output=True,
        timeout=10,
    )
    if probe.returncode:
        pytest.skip("native rootless tmpfs mount unavailable on this host")


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
def test_native_cache_preserves_workload_identity_fds_and_bounded_mount(
    tmp_path: Path, native_ram_capability: None
) -> None:
    output = tmp_path / "native.json"
    read_fd, write_fd = os.pipe()
    script = """
import json, os, pathlib, subprocess, sys
cache=pathlib.Path(os.environ['NODE_COMPILE_CACHE'])
info=os.statvfs(cache)
original=pathlib.Path(str(cache).partition('/root')[2])
os.write(int(sys.argv[2]), b'native-fd')
module=pathlib.Path(sys.argv[1]).with_suffix('.cjs')
module.write_text('module.exports = 42;')
node=subprocess.run(['node','-e','require('+json.dumps(str(module))+'); console.log(require("node:module").getCompileCacheDir())'],check=True,capture_output=True,text=True)
assert node.stdout.strip().startswith(str(cache)) and any(path.is_file() for path in cache.rglob('*'))
pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(uid=os.getuid(),gid=os.getgid(),groups=os.getgroups(),user=os.readlink('/proc/self/ns/user'),mount=os.readlink('/proc/self/ns/mnt'),capacity=info.f_blocks*info.f_frsize,original_empty=not list(original.iterdir()),stdin=sys.stdin.read())))
raise SystemExit(19)
"""
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
            env=_native_environment(),
            input="native-stdin",
            text=True,
            capture_output=True,
            pass_fds=(write_fd,),
            timeout=30,
        )
        assert result.returncode == 19, result.stderr
        observed = json.loads(output.read_text())
        assert observed == dict(
            uid=os.getuid(),
            gid=os.getgid(),
            groups=os.getgroups(),
            user=os.readlink("/proc/self/ns/user"),
            mount=os.readlink("/proc/self/ns/mnt"),
            capacity=owner.CACHE_BYTES,
            original_empty=True,
            stdin="native-stdin",
        )
        assert os.read(read_fd, 9) == b"native-fd"
        assert (
            '"cache": "owned"' in result.stderr
            and '"cleanup": "verified"' in result.stderr
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.process
@pytest.mark.native_resources
def test_native_cache_enospc_after_start_never_reexecutes_workload(
    tmp_path: Path, native_ram_capability: None
) -> None:
    count = tmp_path / "count"
    script = r"""
import errno, os, pathlib, sys
with pathlib.Path(sys.argv[1]).open('a') as count: count.write('once\n')
try:
 with open(pathlib.Path(os.environ['NODE_COMPILE_CACHE'])/'full', 'wb', buffering=0) as output:
  os.posix_fallocate(output.fileno(), 0, 600*1024*1024)
except OSError as error:
 assert error.errno == errno.ENOSPC
 raise SystemExit(27)
raise SystemExit(3)
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
    assert count.read_text() == "once\n" and '"cache": "owned"' in result.stderr


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
def test_native_sigterm_after_ready_settles_original_and_keeper(
    tmp_path: Path, native_ram_capability: None
) -> None:
    marker = tmp_path / "started.json"
    script = "import json,os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'cache':os.environ['NODE_COMPILE_CACHE']})); time.sleep(60)"
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
        keeper = int(Path(started["cache"]).parts[2])
        descriptors = [os.pidfd_open(started["pid"]), os.pidfd_open(keeper)]
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=15)
        assert process.returncode in {-signal.SIGTERM, 128 + signal.SIGTERM}, stderr
        assert '"cache": "owned"' in stderr and '"cleanup": "verified"' in stderr
        for descriptor in descriptors:
            poll = select.poll()
            poll.register(descriptor, select.POLLIN)
            assert poll.poll(1000), "owned native process survived cancellation"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        for descriptor in descriptors:
            os.close(descriptor)


@pytest.mark.process
@pytest.mark.native_resources
def test_authored_node_and_dom_membership_is_identical_with_ram_profile(
    tmp_path: Path,
    native_ram_capability: None,
    native_frontend_toolchain: None,
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
        if mode == "ram":
            prefix = "RAM_TEST_PROFILE: "
            diagnostics = [
                json.loads(line.removeprefix(prefix))
                for line in native.stderr.splitlines()
                if line.startswith(prefix)
            ]
            assert diagnostics == [
                {
                    "cache": "owned",
                    "cleanup": "verified",
                    "native_process_cleanup": "verified",
                    "native_exit_code": 0,
                }
            ]
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
