"""B13 read-only *development* physical evidence preflight.

This diagnostic accepts an operator-provided scope, NOT a verified Fleet journal.
It cannot issue a LACE capability, authenticate a physical witness, or prove
quiescence.  In particular, an empty cgroup, vanished PID, or missing network
name is only a local observation; detached mounts and unknown descendants may
still exist. No result from this module may be used to seal a Run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from oh_goon.core.ports.physical_observation import PhysicalObservationScope as Scope
from oh_goon.integrations import linux_physical_observer as linux


class PreflightError(ValueError):
    """Input cannot identify a bounded original physical resource scope."""


# Compatibility aliases for historic B13-B19 developer fixture tests. No local
# kernel parser lives in the CLI. The mechanism is owned only by linux.
for _name in ("_read_regular", "_birth", "_command", "_component", *linux.PROBES):
    globals()[_name] = getattr(linux, _name)


def parse_scope(raw: object) -> Scope:
    """Require exact fields, not a default that silently drops a dimension."""
    keys = {"run_id", "subject_identity", "cgroup_path", "cgroup_device",
            "cgroup_inode", "tracked_pid", "tracked_birth", "mount_targets",
            "network_namespace", "host_veth", "nft_table"}
    if type(raw) is not dict or set(raw) != keys:
        raise PreflightError("exact original observation scope required")
    def nonempty(name: str) -> str:
        value = raw[name]
        if type(value) is not str or not value or len(value) > 1024 or "\x00" in value:
            raise PreflightError(f"invalid {name}")
        return value
    run_id = nonempty("run_id")
    subject = nonempty("subject_identity")
    if not (subject.startswith("sha256:") and len(subject) == 71 and
            all(x in "0123456789abcdef" for x in subject[7:])):
        raise PreflightError("exact subject digest required")
    path = nonempty("cgroup_path")
    if not Path(path).is_absolute() or ".." in Path(path).parts or path == "/":
        raise PreflightError("absolute bounded cgroup path required")
    for field in ("cgroup_device", "cgroup_inode", "tracked_pid"):
        if type(raw[field]) is not int or raw[field] <= 0:
            raise PreflightError(f"invalid {field}")
    birth = nonempty("tracked_birth")
    if birth.rpartition(":")[0] == "" or not birth.rpartition(":")[2].isdigit():
        raise PreflightError("original boot/starttime identity required")
    targets = raw["mount_targets"]
    if (type(targets) is not list or not targets or len(targets) > 64 or
            any(type(t) is not str or not t.startswith("/") or not t or "\x00" in t or ".." in Path(t).parts for t in targets) or
            len(set(targets)) != len(targets)):
        raise PreflightError("explicit mounted-resource target set required")
    network = (raw["network_namespace"], raw["host_veth"], raw["nft_table"])
    if not (all(x is None for x in network) or all(type(x) is str and 0 < len(x) <= 64 and
                  x.isascii() and x.replace("-", "").replace("_", "").isalnum() for x in network)):
        raise PreflightError("complete bounded network identity or explicit no-network required")
    return Scope(run_id, subject, path, raw["cgroup_device"], raw["cgroup_inode"],
                 raw["tracked_pid"], birth, tuple(targets), *network)



def _network(scope: Scope) -> dict[str, str]:
    return linux._network(scope, command=_command)


def observe(scope: Scope, *, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """Two read-only scans. Never elevates NOT_OBSERVED into physical proof."""
    snapshot = linux.observe_kernel(
        scope, proc_root=proc_root,
        diagnostic_probes={name: globals()[name] for name in linux.PROBES},
    )
    observations = snapshot.to_mapping()
    return {"schema": "oh-goon/b19/local-lace-physical-preflight/v6",
            "run_id": scope.run_id, "subject_identity": scope.subject_identity,
            "classification": "DEVELOPMENT_DIAGNOSTIC_ONLY",
            "kernel_snapshot_identity": snapshot.identity,
            "physical_absence_proven": False, "quiescence_proven": False,
            "teardown_proven": False, "effect_permission": "NONE",
            "observations": observations,
            "proof_obligations": {name: "UNVERIFIED" for name in (
                "ORIGINAL_AUTHENTICATED_RUN", "INDEPENDENT_OBSERVER_CUSTODY",
                "COMPLETE_DESCENDANT_LIFECYCLE", "ALL_MOUNT_NAMESPACES",
                "NETWORK_NAMESPACE_AND_LINK_HANDLES", "DELEGATED_CGROUP_SUBTREE",
                "TRACKED_AND_FOREIGN_NAMESPACE_COVERAGE", "FOREIGN_NAMESPACE_FD_HOLDERS", "VISIBLE_FOREIGN_MOUNT_TARGETS", "VISIBLE_MOUNT_FD_HOLDERS",
                "POST_CLEANUP_CONVERGENCE", "SIGNED_QUIESCENCE_AND_TEARDOWN")},
            "unresolved": [key for key, item in observations.items() if item["state"] != "PRESENT"] +
                ["INDEPENDENT_PROCESS_TREE", "FOREIGN_MOUNT_NAMESPACES", "DETACHED_MOUNTS_UNOBSERVABLE", "OPEN_MOUNT_FD_COVERAGE_UNPROVEN", "FOREIGN_PROC_FD_COMPLETENESS", "KERNEL_RESOURCE_ABSENCE", "AUTHENTICATED_ORIGINAL_JOURNAL"],
            "gate_b": "BLOCKED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only development LACE physical evidence inventory (not a gate)")
    parser.add_argument("--scope", type=Path, required=True, help="Operator-owned original observation JSON; untrusted diagnostic input")
    parser.add_argument("--output", type=Path, required=True, help="New output file; never overwrite")
    args = parser.parse_args(argv)
    try:
        if args.scope.is_symlink() or args.output.exists() or args.output.is_symlink():
            raise PreflightError("unsafe input/output path")
        source = args.scope.read_bytes()
        if len(source) > 16_384:
            raise PreflightError("scope too large")
        report = observe(parse_scope(json.loads(source)))
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write("\n")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"BLOCKED: {exc}\n")
    print("RESULT=DEVELOPMENT_DIAGNOSTIC_ONLY GATE_B=BLOCKED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
