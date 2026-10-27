from __future__ import annotations

import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts/development_test_resources.sh"
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native POSIX shell resource wrapper"
)

HARNESS = """
source "$1"
test_cgroup_root=$2
test_cgroup_directory=$3
shift 3
oh_goon_test_cgroup_root() { printf '%s\\n' "$test_cgroup_root"; }
oh_goon_test_cgroup_directory() { printf '%s\\n' "$test_cgroup_directory"; }
oh_goon_test_is_linux() { return 0; }
printf '%s\\n' "$BASHPID" > "$test_cgroup_directory/cgroup.procs"
"""


def _bash() -> str:
    executable = shutil.which("bash")
    assert executable is not None, "native bash is required for the shell wrapper tests"
    return executable


@pytest.fixture
def cgroup(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "cgroup"
    directory = root / "app.slice/test.scope"
    directory.mkdir(parents=True)
    (root / "cgroup.controllers").write_text("cpu io memory pids\n")
    (directory / "cpu.max").write_text("max 100000\n")
    return root, directory


def _native_runner(tmp_path: Path, status: int = 0) -> tuple[Path, Path]:
    directory = tmp_path / "bin"
    directory.mkdir()
    calls = tmp_path / "systemd-arguments"
    runner = directory / "systemd-run"
    runner.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$OH_GOON_TEST_CPUS" "$@" > {shlex.quote(str(calls))}\n'
        f"exit {status}\n"
    )
    runner.chmod(0o755)
    return directory, calls


def _environment(cpus: str | None, path: Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("OH_GOON_TEST_CPUS", None)
    if cpus is not None:
        environment["OH_GOON_TEST_CPUS"] = cpus
    if path is not None:
        environment["PATH"] = str(path)
    return environment


def _command(cgroup: tuple[Path, Path], *arguments: str, setup: str = "") -> list[str]:
    root, directory = cgroup
    return [
        _bash(),
        "-c",
        HARNESS + setup + '\noh_goon_test_resources_main "$@"\n',
        "_oh_goon_test_resources",
        str(WRAPPER),
        str(root),
        str(directory),
        *arguments,
    ]


def _run(
    tmp_path: Path,
    cgroup: tuple[Path, Path],
    *arguments: str,
    cpus: str | None = None,
    path: Path | None = None,
    setup: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(cgroup, *arguments, setup=setup),
        cwd=tmp_path,
        env=_environment(cpus, path),
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("cpus", ["", "-1", "1.5", "4097", "9" * 30, "two"])
def test_invalid_cpu_budget_fails_before_native_scope_or_workload(
    tmp_path: Path, cgroup: tuple[Path, Path], cpus: str
) -> None:
    binary, calls = _native_runner(tmp_path)
    marker = tmp_path / "started"

    result = _run(
        tmp_path,
        cgroup,
        "/bin/sh",
        "-c",
        'printf started > "$1"',
        "sh",
        str(marker),
        cpus=cpus,
        path=binary,
    )

    assert result.returncode == 2
    assert "CONFIGURATION ERROR" in result.stderr
    assert not calls.exists()
    assert not marker.exists()


def test_explicit_zero_preserves_command_io_directory_environment_and_exit_without_dependencies(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            _bash(),
            str(WRAPPER),
            "/bin/sh",
            "-c",
            'IFS= read -r payload; printf "%s|%s|%s|%s" "$OH_GOON_TEST_CPUS" "$PWD" "$1" "$payload"; '
            "printf workload-error >&2; exit 23",
            "sh",
            "literal $() argument with spaces",
        ],
        cwd=tmp_path,
        env=_environment("0000", tmp_path / "no-tools"),
        input="input payload\n",
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 23
    assert (
        result.stdout == f"0|{tmp_path}|literal $() argument with spaces|input payload"
    )
    assert "workload-error" in result.stderr
    assert "disabled explicitly" in result.stderr


@pytest.mark.parametrize(
    "cpus,resolved", [(None, "2"), ("0004", "4"), ("4096", "4096")]
)
def test_native_scope_receives_resolved_budget_and_exact_command_arguments(
    tmp_path: Path, cgroup: tuple[Path, Path], cpus: str | None, resolved: str
) -> None:
    binary, calls = _native_runner(tmp_path)
    arguments = ("/test/command with spaces", "literal $()", "--option=value")

    result = _run(tmp_path, cgroup, *arguments, cpus=cpus, path=binary)

    assert result.returncode == 0, result.stderr
    forwarded = calls.read_text().splitlines()
    assert forwarded[:9] == [
        resolved,
        "--user",
        "--scope",
        "--slice=app.slice",
        "-p",
        f"CPUQuota={int(resolved) * 100}%",
        "-p",
        "CPUWeight=20",
        "--",
    ]
    assert forwarded[10:] == [
        str(WRAPPER),
        "--oh-goon-cgroup-child",
        resolved,
        "--",
        *arguments,
    ]
    assert "verified" not in result.stderr


@pytest.mark.parametrize("ancestor", [False, True])
def test_verified_existing_cpu_quota_avoids_nested_scopes(
    tmp_path: Path, cgroup: tuple[Path, Path], ancestor: bool
) -> None:
    _, directory = cgroup
    limited = directory.parent if ancestor else directory
    (limited / "cpu.max").write_text("150000 100000\n")
    binary, calls = _native_runner(tmp_path)

    result = _run(
        tmp_path,
        cgroup,
        "/bin/sh",
        "-c",
        'printf "%s" "$OH_GOON_TEST_CPUS"; exit 17',
        path=binary,
    )

    assert result.returncode == 17
    assert result.stdout == "2"
    assert "verified inherited cgroup quota" in result.stderr
    assert not calls.exists()


def test_receiving_child_uses_resolved_argument_and_verifies_native_quota(
    tmp_path: Path, cgroup: tuple[Path, Path]
) -> None:
    _, directory = cgroup
    (directory / "cpu.max").write_text("100000 100000\n")

    result = _run(
        tmp_path,
        cgroup,
        "--oh-goon-cgroup-child",
        "1",
        "--",
        "/bin/sh",
        "-c",
        'printf "%s" "$OH_GOON_TEST_CPUS"',
        cpus="invalid ambient default",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1"


@pytest.mark.parametrize("limit", ["max 100000", "200001 100000", "200000 0", "broken"])
def test_receiving_child_refuses_unproven_quota_even_with_an_ambient_scope_claim(
    tmp_path: Path, cgroup: tuple[Path, Path], limit: str
) -> None:
    _, directory = cgroup
    (directory / "cpu.max").write_text(limit + "\n")
    marker = tmp_path / "started"

    result = _run(
        tmp_path,
        cgroup,
        "--oh-goon-cgroup-child",
        "2",
        "--",
        "/bin/sh",
        "-c",
        'printf started > "$1"',
        "sh",
        str(marker),
        setup="export OH_GOON_TEST_CPU_SCOPE=1",
        cpus="0",
    )

    assert result.returncode == 3
    assert "UNENFORCED" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("unavailable", ["platform", "controller", "systemd"])
def test_unavailable_native_quota_fails_before_work_without_fallback(
    tmp_path: Path, cgroup: tuple[Path, Path], unavailable: str
) -> None:
    root, _ = cgroup
    binary, calls = _native_runner(tmp_path)
    setup = ""
    if unavailable == "platform":
        setup = "oh_goon_test_is_linux() { return 1; }"
    elif unavailable == "controller":
        (root / "cgroup.controllers").write_text("io memory pids\n")
    else:
        binary = tmp_path / "missing-tools"

    result = _run(
        tmp_path, cgroup, "/bin/sh", "-c", "printf started", path=binary, setup=setup
    )

    assert result.returncode == 3
    assert "UNENFORCED" in result.stderr
    assert result.stdout == ""
    assert not calls.exists()


def test_failed_native_scope_preserves_failure_and_never_starts_workload(
    tmp_path: Path, cgroup: tuple[Path, Path]
) -> None:
    binary, calls = _native_runner(tmp_path, status=19)
    marker = tmp_path / "started"

    result = _run(
        tmp_path,
        cgroup,
        "/bin/sh",
        "-c",
        'printf started > "$1"',
        "sh",
        str(marker),
        path=binary,
    )

    assert result.returncode == 19
    assert calls.is_file()
    assert not marker.exists()
    assert "verified" not in result.stderr


def test_unrelated_cgroup_leaf_cannot_supply_an_inherited_quota(
    tmp_path: Path, cgroup: tuple[Path, Path]
) -> None:
    _, directory = cgroup
    (directory / "cpu.max").write_text("100000 100000\n")
    binary, calls = _native_runner(tmp_path)

    result = _run(
        tmp_path,
        cgroup,
        "/bin/sh",
        "-c",
        "printf started",
        path=binary,
        setup='printf "0\\n" > "$test_cgroup_directory/cgroup.procs"',
    )

    assert result.returncode == 3
    assert "does not prove membership" in result.stderr
    assert result.stdout == ""
    assert not calls.exists()


def test_native_scope_transports_the_strictest_private_caller_task_limit(
    tmp_path: Path, cgroup: tuple[Path, Path]
) -> None:
    root, directory = cgroup
    nested = directory / "nested.scope"
    nested.mkdir()
    (nested / "cpu.max").write_text("max 100000\n")
    (nested / "pids.max").write_text("70685\n")
    (directory / "pids.max").write_text("4096\n")
    (directory.parent / "pids.max").write_text("2048\n")
    binary, calls = _native_runner(tmp_path)

    result = _run(tmp_path, (root, nested), "/bin/true", path=binary)

    assert result.returncode == 0, result.stderr
    forwarded = calls.read_text().splitlines()
    assert forwarded[8:11] == ["-p", "TasksMax=4096", "--"]
    assert forwarded[-7:] == [
        str(WRAPPER),
        "--oh-goon-cgroup-child",
        "2",
        "--tasks-max",
        "4096",
        "--",
        "/bin/true",
    ]


@pytest.mark.parametrize(
    "native_limit,expected", [("max", 3), ("4097", 3), ("4096", 0), ("2048", 0)]
)
def test_receiving_child_verifies_the_transported_native_task_ceiling(
    tmp_path: Path, cgroup: tuple[Path, Path], native_limit: str, expected: int
) -> None:
    _, directory = cgroup
    (directory / "cpu.max").write_text("200000 100000\n")
    (directory / "pids.max").write_text(native_limit + "\n")

    result = _run(
        tmp_path,
        cgroup,
        "--oh-goon-cgroup-child",
        "2",
        "--tasks-max",
        "4096",
        "--",
        "/bin/sh",
        "-c",
        "printf started",
    )

    assert result.returncode == expected, result.stderr
    if expected:
        assert result.stdout == ""
        assert "resolved task limit" in result.stderr
    else:
        assert result.stdout == "started"


@pytest.mark.parametrize(
    "control,value",
    [
        ("memory.max", "268435456"),
        ("memory.high", "268435456"),
        ("memory.swap.max", "0"),
        ("memory.swap.high", "0"),
        ("memory.zswap.max", "0"),
    ],
)
@pytest.mark.parametrize("private_parent", [False, True])
def test_private_memory_ceiling_refuses_scope_movement_before_work(
    tmp_path: Path,
    cgroup: tuple[Path, Path],
    control: str,
    value: str,
    private_parent: bool,
) -> None:
    root, directory = cgroup
    if private_parent:
        nested = directory / "nested.scope"
        nested.mkdir()
        (nested / "cpu.max").write_text("max 100000\n")
        cgroup = root, nested
    (directory / control).write_text("max\n")
    binary, calls = _native_runner(tmp_path)

    baseline = _run(tmp_path, cgroup, "/bin/true", path=binary)
    assert baseline.returncode == 0, baseline.stderr
    assert calls.exists()
    calls.unlink()
    (directory / control).write_text(value + "\n")

    result = _run(tmp_path, cgroup, "/bin/sh", "-c", "printf started", path=binary)

    assert result.returncode == 3
    assert f"{control} constraint" in result.stderr
    assert result.stdout == ""
    assert not calls.exists()


def test_shared_memory_pressure_limits_do_not_prevent_native_cpu_scope(
    tmp_path: Path, cgroup: tuple[Path, Path]
) -> None:
    _, directory = cgroup
    (directory.parent / "memory.high").write_text("268435456\n")
    (directory.parent / "memory.swap.max").write_text("0\n")
    binary, calls = _native_runner(tmp_path)

    result = _run(tmp_path, cgroup, "/bin/true", path=binary)

    assert result.returncode == 0, result.stderr
    assert calls.exists()


def test_inherited_cpu_quota_keeps_private_memory_and_task_constraints_in_place(
    tmp_path: Path, cgroup: tuple[Path, Path]
) -> None:
    _, directory = cgroup
    (directory / "cpu.max").write_text("200000 100000\n")
    (directory / "memory.max").write_text("268435456\n")
    (directory / "memory.high").write_text("134217728\n")
    (directory / "memory.swap.max").write_text("0\n")
    (directory / "memory.swap.high").write_text("0\n")
    (directory / "memory.zswap.max").write_text("0\n")
    (directory / "pids.max").write_text("128\n")
    binary, calls = _native_runner(tmp_path)

    result = _run(tmp_path, cgroup, "/bin/sh", "-c", "printf started", path=binary)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "started"
    assert not calls.exists()


@pytest.mark.process
@pytest.mark.parametrize("cpus", ["0", "2"])
def test_exec_preserves_process_identity_and_sigint_for_owned_descendants(
    tmp_path: Path, cgroup: tuple[Path, Path], cpus: str
) -> None:
    _, directory = cgroup
    (directory / "cpu.max").write_text("200000 100000\n")
    program = (
        "import os,subprocess,sys\n"
        "child=subprocess.Popen([sys.executable,'-S','-c','import time; time.sleep(30)'])\n"
        "print(os.getpid(), child.pid, flush=True)\n"
        "try: child.wait()\n"
        "except KeyboardInterrupt:\n"
        " child.wait(timeout=5)\n"
        " print('descendant-closed', flush=True)\n"
        " raise SystemExit(130)\n"
    )
    process = subprocess.Popen(
        _command(cgroup, sys.executable, "-S", "-c", program),
        cwd=tmp_path,
        env=_environment(cpus),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 10)
        assert ready
        parent_pid, _child_pid = map(int, process.stdout.readline().split())
        assert parent_pid == process.pid
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stderr
        assert "descendant-closed" in stdout
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
