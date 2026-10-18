"""P1/B27B1: original Bubblewrap builder may not invent/omit native mounts."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import fleet_plan_bubblewrap_isolation as bwrap
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b1_native_mount_roster,
)
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _request

PACKAGE = ROOT / "backend/src/oh_goon"


def prepared(tmp_path: Path, monkeypatch):
    request = _request(tmp_path)
    request.attempt.egress.mode = "none"
    paths = request.attempt.paths
    paths.etc = paths.root / "etc"
    paths.home = paths.root / "home"
    paths.tmp = paths.root / "tmp"
    paths.config = paths.root / "config"
    atomic = SimpleNamespace(
        bubblewrap_bindings=(
            SimpleNamespace(descriptor=22, sandbox_path=PurePosixPath("/bin/payload"), mode=0o755),
            SimpleNamespace(
                descriptor=23, sandbox_path=PurePosixPath("/bin/interpreter"), mode=0o555
            ),
        )
    )
    monkeypatch.setattr(bwrap, "verify_runtime_root", lambda _: None)
    args = bwrap._prepare_mounts(
        "/bin/true", request, atomic_authority=atomic, network_prefix=(), nested_cgroup_root=None
    )
    return request, atomic, args


def test_one_original_builder_covers_root_writable_etc_and_atomic_fd(tmp_path, monkeypatch):
    request, atomic, args = prepared(tmp_path, monkeypatch)
    bwrap._require_original_native_mount_argv(args, request, atomic, None)
    assert "--ro-bind-data" in args and "--perms" in args
    assert "--ro-bind" in args and "--bind" in args
    assert args.count("--ro-bind-data") == 2
    assert args.count("--proc") == args.count("--dev") == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "root",
        "home",
        "config",
        "etc",
        "workspace",
        "drop",
        "extra",
        "fd",
        "mode",
        "target",
        "proc",
        "dev",
        "bind-mode",
        "unregistered",
    ],
)
def test_native_argv_substitution_denied(tmp_path, monkeypatch, mutation):
    request, atomic, args = prepared(tmp_path, monkeypatch)
    args = list(args)
    if mutation == "root":
        args[args.index(str(request.attempt.runtime_root.root))] = "/foreign-root"
    elif mutation == "home":
        args[args.index(str(request.attempt.paths.home))] = "/foreign-home"
    elif mutation == "config":
        args[args.index(str(request.attempt.paths.config))] = "/foreign-config"
    elif mutation == "etc":
        args[args.index(str(request.attempt.paths.etc / "hosts"))] = "/foreign-hosts"
    elif mutation == "workspace":
        args[args.index(str(request.attempt.paths.workspace))] = "/foreign-workspace"
    elif mutation == "drop":
        at = args.index("--bind")
        del args[at : at + 3]
    elif mutation == "extra":
        args.extend(["--bind", "/unauthorized", "/unauthorized"])
    elif mutation == "fd":
        at = args.index("--ro-bind-data")
        args[at + 1] = "40"
    elif mutation == "mode":
        at = args.index("--ro-bind-data")
        args[at - 1] = "777"
    elif mutation == "target":
        at = args.index("--ro-bind-data")
        args[at + 2] = "/different"
    elif mutation == "unregistered":
        args.extend(["--dev-bind", "/foreign", "/foreign"])
    elif mutation in ("proc", "dev"):
        at = args.index("--" + mutation)
        args[at + 1] = "/different"
    else:
        at = args.index("--bind")
        args[at] = "--ro-bind"
    with pytest.raises(FleetPlanInvalid, match="native mount|synthetic native|sealed runtime"):
        bwrap._require_original_native_mount_argv(args, request, atomic, None)


def test_nested_cgroup_bind_source_is_required(tmp_path, monkeypatch):
    request, atomic, args = prepared(tmp_path, monkeypatch)
    cgroup = tmp_path / "native-cgroup"
    args += ["--bind", str(cgroup), "/sys/fs/cgroup"]
    bwrap._require_original_native_mount_argv(args, request, atomic, cgroup)
    with pytest.raises(FleetPlanInvalid, match="native mount"):
        bwrap._require_original_native_mount_argv(args, request, atomic, tmp_path / "foreign")


def test_x0_guard_is_part_of_normal_original_architecture():
    assert _check_p1b27b1_native_mount_roster(PACKAGE) == []


@pytest.mark.parametrize(
    "mutation", ["skip", "dead", "remove-root", "remove-writable", "remove-fd", "disable-rejection"]
)
def test_x0_prevents_future_mount_roster_drift(tmp_path, mutation):
    p = tmp_path / "integrations" / "fleet_plan_bubblewrap_isolation.py"
    p.parent.mkdir(parents=True)
    code = (PACKAGE / "integrations/fleet_plan_bubblewrap_isolation.py").read_text()
    if mutation == "skip":
        code = code.replace(
            "    _require_original_native_mount_argv(args, request, atomic_authority, nested_cgroup_root)\n",
            "",
            1,
        )
    elif mutation == "dead":
        code = code.replace(
            "    _require_original_native_mount_argv(args, request, atomic_authority, nested_cgroup_root)\n",
            "    if False:\n        _require_original_native_mount_argv(args, request, atomic_authority, nested_cgroup_root)\n",
            1,
        )
    elif mutation == "remove-root":
        code = code.replace(
            "('--ro-bind', str(attempt.runtime_root.root), '/')",
            "('--ro-bind', '/foreign', '/')",
            1,
        )
    elif mutation == "remove-writable":
        before, guard = code.split("def _require_original_native_mount_argv(", 1)
        code = (
            before
            + "def _require_original_native_mount_argv("
            + guard.replace("attempt.paths.home", "attempt.paths.workspace", 1)
        )
    elif mutation == "remove-fd":
        before, guard = code.split("def _require_original_native_mount_argv(", 1)
        code = (
            before
            + "def _require_original_native_mount_argv("
            + guard.replace("for f in atomic_authority.bubblewrap_bindings", "for f in ()", 1)
        )
    else:
        code = code.replace("or sorted(observed) != sorted(expected)", "or False", 1)
    p.write_text(code)
    assert _check_p1b27b1_native_mount_roster(tmp_path)


def test_wsl_enrollment_and_exact_manifest_selector():
    text = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    selection = "if [[ -f .checkpoint/P1B27B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B1_SOURCE.sha256'; fi"
    assert text.count(selection) == 1
    assert text.index(selection) < text.index("if [[ -f .checkpoint/P1B27B2A_SOURCE.sha256 ]]")
    assert "--require-module test_p1b27b1_native_mount_roster" in text
    assert "observer_tests+=(tests/unit/runtime/test_p1b27b1_native_mount_roster.py)" in text
