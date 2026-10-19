"""Opt-in REAL Linux Bubblewrap mechanical witness using the ORIGINAL mount builder.

This test must FAIL (not skip/pass) when bwrap, user namespaces, or FD-bind
features are unavailable. It is NOT independently signed Run admission,
independent LACE custody, full physical absence, Gate B or Agent permission.
Only the existing QENV1 PHYSICAL_OBSERVER selection may run this test on WSL.
"""

from __future__ import annotations

import fcntl
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.runtime.test_p1b27b2b2a_pinned_bind_source import _prepared

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.engine.atomic_execution_bytes import _linux_seal_operations
from oh_goon.engine.command import (
    CommandAuthorityTerminatedError,
    CommandCancelledError,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.integrations import fleet_plan_bubblewrap_isolation as owner
from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
from oh_goon.integrations._fleet_plan_runtime_root import (
    capture_runtime_root,
    verify_runtime_root,
)
from oh_goon.integrations.content_snapshot import snapshot_host_path
from oh_goon.integrations.fleet_plan_bind_source_custody import (
    OriginalBindSourceLease,
    require_bubblewrap_fd_binding,
)
from oh_goon.integrations.fleet_plan_local_lace_intent import local_resource_identities


class _AtomicMechanicalFixture:
    """Test-owned sealed bytes, NOT a second atomic authority implementation."""

    def __init__(self, fd: int) -> None:
        self.pass_fds = (fd,)
        self.bubblewrap_bindings = (
            SimpleNamespace(descriptor=fd, sandbox_path=Path("/tmp/oh-goon-fd-proof"), mode=0o444),
        )
        self.fd = fd

    def verify_kernel_custody(self) -> None:
        _, get_seals, required = _linux_seal_operations()
        seals = fcntl.fcntl(self.fd, get_seals)
        assert seals & required == required

    def rewind_for_bubblewrap(self) -> None:
        self.verify_kernel_custody()
        os.lseek(self.fd, 0, os.SEEK_SET)


def _pinned_kernel_mount_identity(lease: OriginalBindSourceLease, target: str) -> str:
    """Expected source device/inode from the ORIGINAL pinned FD, not a pathname.

    The real Bubblewrap witness compares this with stat(2) of the mounted
    target *inside* the child. This is mechanical source-consumption evidence,
    never independent observer custody or a signed Run fact.
    """
    lease.verify_kernel_custody()
    matching = [pin for pin in lease.pins if pin.target == target]
    if len(matching) != 1:
        raise AssertionError("exactly one original pinned mount target required")
    source = matching[0]
    info = os.fstat(source.fd)
    assert (info.st_dev, info.st_ino) == (source.device, source.inode)
    return f"{info.st_dev}:{info.st_ino}"


def _prepared_effectful_runtime_root(destination: Path):
    """Prepare this witness through the ONE production runtime-root entrypoint.

    Test utilities are declared as frozen HostToolBindings and mounted through
    the ORIGINAL toolchain consumer. No manual utility/dependency copies, no
    host-root shortcut, no independent root identity, no verifier monkeypatch.
    """
    inspector_name = shutil.which("ldd")
    if inspector_name is None:
        pytest.fail("BLOCKED: original runtime dependency inspector ldd missing")
    toolchain = destination.parent / "declared-host-tools"
    tools: list[HostToolBinding] = []
    for name in ("sh", "cat", "stat", "awk", "sleep"):
        executable = shutil.which(name)
        if executable is None:
            pytest.fail(f"BLOCKED: declared native host tool missing: {name}")
        source = Path(executable).resolve(strict=True)
        snapshot = snapshot_host_path(
            source,
            toolchain / name,
            label=f"Original declared witness host tool {name}",
        )
        tools.append(
            HostToolBinding(
                name=name,
                source_path=source,
                snapshot_path=snapshot.snapshot_path,
                identity=snapshot.identity,
            )
        )
    runtime = capture_runtime_root(
        tuple(tools),
        {},
        destination,
        runtime_inspector=Path(inspector_name),
        network_enabled=False,
        cancel_requested=None,
    )
    verify_runtime_root(runtime)
    return runtime, tuple(tools)


def test_effectful_fixture_has_original_mount_scaffold(tmp_path: Path) -> None:
    """Regression for WSL EROFS at /workspace before FD mount consumption."""
    runtime, tools = _prepared_effectful_runtime_root(tmp_path / "effectful-root")
    root = runtime.root
    verify_runtime_root(runtime)
    assert {tool.name for tool in tools} == {"sh", "cat", "stat", "awk", "sleep"}
    for target in (
        "workspace",
        "home/fleet",
        "tmp",
        "cache",
        "config",
        "etc",
        "proc",
        "dev",
        "toolchain/bin",
    ):
        assert (root / target).is_dir(), target
    for target in (
        "etc/passwd",
        "etc/group",
        "etc/nsswitch.conf",
        "etc/hosts",
        "bin/sh",
        "toolchain/bin/cat",
        "toolchain/bin/stat",
        "toolchain/bin/awk",
        "toolchain/bin/sleep",
    ):
        assert (root / target).is_file(), target
    assert str(root) != "/" and root.is_relative_to(tmp_path)


def test_canonical_effectful_runtime_root_rejects_post_capture_tampering(tmp_path: Path) -> None:
    """Frozen root identity must fail before any native mount effect."""
    binding, _tools = _prepared_effectful_runtime_root(tmp_path / "frozen-root")
    (binding.root / "etc/passwd").write_bytes(b"fixture-tamper-after-capture")
    with pytest.raises(FleetPlanInvalid, match="changed after Fleet bound"):
        verify_runtime_root(binding)


@contextmanager
def _original_effectful_fd_mount_command(tmp_path: Path, sandbox_shell: str):
    """One original native mount launch construction shared by all effectful tests.

    This does not add production authority or a new execution path: same pinned
    lease, same frozen runtime-root entrypoint, same original Bubblewrap command.
    """
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.fail("BLOCKED: real Bubblewrap required; no fake binary or mechanical PASS")
    require_bubblewrap_fd_binding(executable)
    request = _prepared(tmp_path)
    request.attempt.egress.mode = "none"
    runtime, tools = _prepared_effectful_runtime_root(tmp_path / "effectful-root")
    request.attempt.runtime_root = runtime
    request.attempt.host_tools = tools
    (request.attempt.paths.tmp / "oh-goon-fd-proof").touch()
    (request.attempt.paths.home / "kernel-fd-marker").write_text("native-FD-source-v1\n")
    fd = os.memfd_create("b27-effectful-fixture", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.write(fd, b"sealed-FD-data-v1\n")
        add_seals, _, required_seals = _linux_seal_operations()
        fcntl.fcntl(fd, add_seals, required_seals)
        atomic = _AtomicMechanicalFixture(fd)
        with OriginalBindSourceLease.pin_prepared(request) as lease:
            verify_runtime_root(request.attempt.runtime_root)
            backend = owner.BubblewrapProcessIsolation(executable)
            pinned_home = _pinned_kernel_mount_identity(lease, "/home/fleet")
            pinned_workspace = _pinned_kernel_mount_identity(lease, "/workspace")
            launch = SimpleNamespace(
                cwd="/workspace",
                argv=(
                    "/bin/sh",
                    "-c",
                    sandbox_shell,
                    "child-args",
                    "--bind-fd",
                    "999",
                    "/child-only-fake-mount",
                    pinned_home,
                    pinned_workspace,
                ),
                env={"PATH": "/toolchain/bin"},
            )
            argv, env = backend._command(
                request,
                launch=launch,
                atomic_authority=atomic,
                network_prefix=(),
                signed_mounts_identity=local_resource_identities(request)["mounts"],
                mount_source_lease=lease,
            )
            atomic.rewind_for_bubblewrap()
            inherited = owner._require_original_native_fd_handoff(argv, atomic, lease)
            yield argv, env, inherited
    finally:
        os.close(fd)


def test_original_fd_bind_argv_is_consumed_by_real_bubblewrap(tmp_path: Path) -> None:
    """Effectful kernel mount/namespace witness, NEVER synthetic success."""
    sandbox_shell = """set -eu
      test "$1" = '--bind-fd'
      test "$2" = '999'
      test "$3" = '/child-only-fake-mount'
      test "$(cat /home/fleet/kernel-fd-marker)" = 'native-FD-source-v1'
      test "$(cat /tmp/oh-goon-fd-proof)" = 'sealed-FD-data-v1'
      # Compare actual kernel objects, not only contents or mount IDs.
      # Host expectations come from the same ORIGINAL pre-effect pinned FDs.
      test "$(stat -Lc '%d:%i' /home/fleet)" = "$4"
      test "$(stat -Lc '%d:%i' /workspace)" = "$5"
      # --ro-bind-data materializes sealed bytes and need not preserve FD inode.
      for target in /home/fleet /workspace /tmp/oh-goon-fd-proof; do
        awk -v target="$target" '$5 == target { count++; id=$1 } END { if (count != 1 || id !~ /^[1-9][0-9]*$/) exit 88 }' /proc/self/mountinfo
      done
      test -r /proc/self/ns/mnt
      printf 'ORIGINAL_FD_MOUNTS_VISIBLE\\n'
    """
    with _original_effectful_fd_mount_command(tmp_path, sandbox_shell) as (argv, env, inherited):
        result = run_command(*argv, timeout=30, cwd=None, env=env, pass_fds=inherited)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ORIGINAL_FD_MOUNTS_VISIBLE"
        assert result.supervisor.final_process_group_quiescent


@pytest.mark.parametrize("interruption", ("cancel", "trust_revoke", "timeout", "orphan"))
def test_original_effectful_fd_mount_lifecycle_is_supervised(
    tmp_path: Path, interruption: str
) -> None:
    """Opt-in kernel lifecycle test: original launch + original process supervisor.

    A post-launch terminal group receipt is NOT independently authenticated
    LACE teardown/absence proof. Real native bwrap is mandatory; no fake fallback.
    """
    sandbox_shell = r"""set -eu
      test "$1" = '--bind-fd'
      test "$2" = '999'
      test "$3" = '/child-only-fake-mount'
      test "$(cat /home/fleet/kernel-fd-marker)" = 'native-FD-source-v1'
      test "$(cat /tmp/oh-goon-fd-proof)" = 'sealed-FD-data-v1'
      test "$(stat -Lc '%d:%i' /home/fleet)" = "$4"
      test "$(stat -Lc '%d:%i' /workspace)" = "$5"
      for target in /home/fleet /workspace /tmp/oh-goon-fd-proof; do
        awk -v target="$target" '$5 == target { count++; id=$1 } END { if (count != 1 || id !~ /^[1-9][0-9]*$/) exit 88 }' /proc/self/mountinfo
      done
      sleep 20 &
      echo P3_NATIVE_CHILD_READY
      if test "$P3_LIFECYCLE_MODE" = orphan; then exit 0; fi
      wait
    """
    # Mode supplied solely as child script input; it does not decide mounts.
    script = sandbox_shell.replace("$P3_LIFECYCLE_MODE", interruption)
    with _original_effectful_fd_mount_command(tmp_path, script) as (argv, env, inherited):
        readiness = []

        def stdout_seen(stream: str, output: str) -> None:
            if stream == "stdout" and "P3_NATIVE_CHILD_READY" in output:
                readiness.append(True)

        common = dict(
            cwd=None,
            env=env,
            pass_fds=inherited,
            stream_output=stdout_seen,
            require_process_group_quiescence=True,
        )
        if interruption == "cancel":
            with pytest.raises(CommandCancelledError) as raised:
                run_command(*argv, timeout=5, cancel_requested=lambda: bool(readiness), **common)
            assert raised.value.supervisor.cleanup_reason == "cancel"
            receipt = raised.value.supervisor
        elif interruption == "trust_revoke":
            with pytest.raises(CommandAuthorityTerminatedError) as raised:
                run_command(
                    *argv,
                    timeout=5,
                    termination_requested=lambda: (
                        TerminationSignal(cause="trust_revoke", reason="native-witness-revocation")
                        if readiness
                        else None
                    ),
                    **common,
                )
            assert raised.value.request.cause == "trust_revoke"
            assert raised.value.supervisor.cleanup_reason == "authority"
            receipt = raised.value.supervisor
        elif interruption == "timeout":
            with pytest.raises(CommandTimeoutExpired) as raised:
                run_command(*argv, timeout=2, **common)
            assert raised.value.supervisor.cleanup_reason == "timeout"
            receipt = raised.value.supervisor
        else:
            result = run_command(*argv, timeout=5, **common)
            assert result.returncode == 0, result.stderr
            receipt = result.supervisor
            # bwrap may itself reap child namespace processes. If not, the
            # ORIGINAL supervisor must enter its own descendant cleanup.
            if receipt.leader_exited_before_group_quiescence:
                assert receipt.process_group_cleanup_requested
                assert receipt.cleanup_reason == "output_cleanup"
        assert readiness, "real child did not reach mounted execution before termination"
        assert receipt.final_process_group_quiescent, "process-group teardown unproven"
