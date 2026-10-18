"""B27B2B2A: Linux kernel bind-source FD custody, no pathname fallback."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bind_source_custody import (
    OriginalBindSourceLease,
    _open_absolute_no_symlinks,
    require_bubblewrap_fd_binding,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b2b2a_source_fd_custody,
)
from tests.factories.x0_architecture import replace_python_syntax
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _request

PACKAGE = ROOT / "backend/src/oh_goon"


def _prepared(tmp_path: Path):
    request = _request(tmp_path)
    paths = request.attempt.paths
    for folder in (
        request.attempt.runtime_root.root,
        paths.workspace,
        paths.home,
        paths.tmp,
        paths.cache,
        paths.config,
        paths.etc,
    ):
        folder.mkdir(parents=True, exist_ok=True)
    for name in ("passwd", "group", "nsswitch.conf", "hosts"):
        (paths.etc / name).write_text(name)
    return request


def _argv(lease):
    return [
        "bwrap",
        *[v for p in lease.pins for v in (p.option, p.source, p.target)],
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--",
        "/bin/true",
    ]


def test_actual_linux_source_fds_replace_all_pathnames(tmp_path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        original = _argv(lease)
        lease.consume_validated_argv(original)
        lease.verify_fd_argv(original)
        assert all(
            "--bind-fd" == original[i] or "--ro-bind-fd" == original[i]
            for i, x in enumerate(original)
            if x in ("--bind-fd", "--ro-bind-fd")
        )
        assert all(p.fd in lease.pass_fds and os.fstat(p.fd).st_ino == p.inode for p in lease.pins)
        assert all(p.source not in original for p in lease.pins)
    with pytest.raises(FleetPlanInvalid, match="closed"):
        lease.verify_kernel_custody()


def test_replace_path_after_pinning_still_pins_original_kernel_object(tmp_path):
    request = _prepared(tmp_path)
    original = request.attempt.paths.home
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        pinned = next(p for p in lease.pins if p.source == str(original))
        before = os.fstat(pinned.fd)
        original.rename(original.with_name("home-old"))
        original.mkdir()
        assert os.stat(original).st_ino != before.st_ino
        assert (os.fstat(pinned.fd).st_dev, os.fstat(pinned.fd).st_ino) == (
            before.st_dev,
            before.st_ino,
        )
        argv = _argv(lease)
        lease.consume_validated_argv(argv)
        assert str(original) not in argv
        assert ["--bind-fd", str(pinned.fd), "/home/fleet"] == argv[
            argv.index(str(pinned.fd)) - 1 : argv.index(str(pinned.fd)) + 2
        ]


def test_fd_replacement_by_same_contents_different_inode_fails(tmp_path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        pinned = lease.pins[0]
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        other_fd = _open_absolute_no_symlinks(str(replacement))
        try:
            os.dup2(other_fd, pinned.fd)
            with pytest.raises(FleetPlanInvalid, match="kernel object substituted"):
                lease.verify_kernel_custody()
        finally:
            os.close(other_fd)


@pytest.mark.parametrize(
    "mutation", ["extra", "remove", "path-fallback", "wrong-fd", "wrong-target"]
)
def test_fd_argv_mutations_fail_closed(tmp_path, mutation):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        args = _argv(lease)
        lease.consume_validated_argv(args)
        i = args.index("--ro-bind-fd")
        if mutation == "extra":
            args[i:i] = ["--bind-fd", "999", "/foreign"]
        elif mutation == "remove":
            del args[i : i + 3]
        elif mutation == "path-fallback":
            args[i] = "--ro-bind"
        elif mutation == "wrong-fd":
            args[i + 1] = "999"
        else:
            args[i + 2] = "/foreign"
        with pytest.raises(FleetPlanInvalid, match="FD bind argv"):
            lease.verify_fd_argv(args)


@pytest.mark.parametrize("source", ["symlink-leaf", "symlink-parent", "missing", "dotdot"])
def test_symlink_and_noncanonical_paths_never_pinned(tmp_path, source):
    good = tmp_path / "base"
    good.mkdir()
    real = good / "real"
    real.mkdir()
    if source == "symlink-leaf":
        (good / "link").symlink_to(real, target_is_directory=True)
        path = good / "link"
    elif source == "symlink-parent":
        (tmp_path / "link").symlink_to(good, target_is_directory=True)
        path = tmp_path / "link" / "real"
    elif source == "missing":
        path = good / "missing"
    else:
        path = good / ".." / "base" / "real"
    with pytest.raises((FleetPlanInvalid, OSError)):
        fd = _open_absolute_no_symlinks(str(path))
        os.close(fd)


def test_capability_check_fails_closed_when_fd_flags_unavailable(tmp_path):
    bad = tmp_path / "older-bwrap"
    bad.write_text('#!/bin/sh\necho "--bind SRC DEST --ro-bind SRC DEST"\n')
    bad.chmod(0o755)
    with pytest.raises(FleetPlanInvalid, match="--bind-fd"):
        require_bubblewrap_fd_binding(str(bad))
    good = tmp_path / "newer-bwrap"
    good.write_text('#!/bin/sh\necho "--bind-fd FD DEST --ro-bind-fd FD DEST"\n')
    good.chmod(0o755)
    require_bubblewrap_fd_binding(str(good))


def test_new_x0_guard_is_integrated_and_detects_bypass(tmp_path):
    assert _check_p1b27b2b2a_source_fd_custody(PACKAGE) == []
    import shutil

    bwrap = tmp_path / "integrations" / "fleet_plan_bubblewrap_isolation.py"
    bwrap.parent.mkdir(parents=True)
    shutil.copy2(PACKAGE / "integrations/fleet_plan_bubblewrap_isolation.py", bwrap)
    bwrap.write_text(
        replace_python_syntax(
            bwrap.read_text(), "mount_source_lease.consume_validated_argv(args)", "pass"
        )
    )
    assert _check_p1b27b2b2a_source_fd_custody(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "skip-capability",
        "dead-capability",
        "missing-pin",
        "skip-rewrite",
        "remove-fd-pass",
        "remove-prelaunch-check",
        "remove-inode-check",
        "allow-symlinks",
        "partial-roster",
    ],
)
def test_x0_rejects_pinned_fd_custody_drift(tmp_path, mutation):
    import shutil

    for name in ("fleet_plan_bubblewrap_isolation.py", "fleet_plan_bind_source_custody.py"):
        dst = tmp_path / "integrations" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PACKAGE / "integrations" / name, dst)
    bwrap = tmp_path / "integrations/fleet_plan_bubblewrap_isolation.py"
    owner = tmp_path / "integrations/fleet_plan_bind_source_custody.py"
    path = (
        owner if mutation in ("remove-inode-check", "allow-symlinks", "partial-roster") else bwrap
    )
    original = path.read_text()
    changes = {
        "skip-capability": (
            "        require_bubblewrap_fd_binding(self.executable)",
            "        pass",
        ),
        "dead-capability": (
            "        require_bubblewrap_fd_binding(self.executable)",
            "        if False:\n            require_bubblewrap_fd_binding(self.executable)",
        ),
        "missing-pin": (
            "with OriginalBindSourceLease.pin_prepared(request) as mount_source_lease:",
            "with nullcontext(None) as mount_source_lease:",
        ),
        "skip-rewrite": ("        mount_source_lease.consume_validated_argv(args)", "        pass"),
        "remove-fd-pass": ("pass_fds=inherited_fds", "pass_fds=()"),
        "remove-prelaunch-check": ("    mount_source_lease.verify_fd_argv(list(argv))", "    pass"),
        "remove-inode-check": (
            "source.device, source.inode, source.mode",
            "info.st_dev, info.st_ino, info.st_mode",
        ),
        "allow-symlinks": ("os.O_NOFOLLOW | os.O_CLOEXEC", "os.O_CLOEXEC"),
        "partial-roster": ("for tool in attempt.host_tools:", "for tool in ():"),
    }
    before, after = changes[mutation]
    path.write_text(replace_python_syntax(original, before, after))
    assert _check_p1b27b2b2a_source_fd_custody(tmp_path), mutation
