"""B12D2C2: bounded local orphan cleanup through existing native owners.

Only the original Fleet recovery owner may call the callback under its SQLite
write cut. Hold original allocator locks across fresh kernel observation and
all native effects. Never unlink recovery records or claim full quiescence.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.original_native_custody import OriginalNativeCustodyRequirement
from oh_goon.core.ports.local_lace_cleanup import (
    LocalLaceCleanupTicket,
    NativeCleanupFailure,
    NativeCleanupResult,
)
from oh_goon.core.ports.local_lace_orphan_inventory import (
    LocalLaceOrphanInventory,
    LocalLaceOrphanSubject,
)
from oh_goon.integrations._fleet_plan_cgroup_resources import (
    _bound_cgroup_directory,
    _process_identity,
    _remove_cgroup,
    _resource_lock,
    _wait_for_cgroup_empty,
    require_independent_native_parent_custody,
)
from oh_goon.integrations._fleet_plan_restricted_egress import (
    _cleanup_egress_resources,
    _egress_allocation_lock,
)
from oh_goon.integrations.fleet_plan_local_lace_orphan_inventory import (
    _matching_record,
    rediscover_local_orphan,
)


def cleanup_local_orphan(
    owner: object,
    ticket: LocalLaceCleanupTicket,
    subject: LocalLaceOrphanSubject,
    inventory: LocalLaceOrphanInventory,
    *,
    cgroup_root: Path,
    cgroup_state_dir: Path,
    cgroup_lock: Path,
    network_state_dir: Path,
    network_lock: Path,
    ip: str,
    nft: str,
) -> NativeCleanupResult:
    """Attempt real native deletion; never produce an absence or terminal receipt.

    Refuse populated or unknown process trees; B12E handles quiescence and
    stronger full process/mount proofs. Records deliberately remain after
    successful deletion for crash recovery and independent verification.
    """
    if (
        type(ticket) is not LocalLaceCleanupTicket
        or type(subject) is not LocalLaceOrphanSubject
        or type(inventory) is not LocalLaceOrphanInventory
        or ticket.run_id != subject.run_id
        or ticket.subject_identity != subject.subject_identity
        or subject.control_epoch != ticket.control_epoch
        or inventory.subject_identity != subject.subject_identity
        or inventory.state != "OBSERVED_PRESENT"
        or inventory.cgroup_processes
        or inventory.cgroup_inode is None
        or inventory.cgroup_device is None
    ):
        raise FleetPlanInvalid("original observed empty orphan required for native recovery")
    # Take BOTH original native allocator locks, never an alternative lock plane.
    with ExitStack() as locks:
        locks.enter_context(_resource_lock(cgroup_lock))
        locks.enter_context(_egress_allocation_lock(network_lock))

        def native_action() -> NativeCleanupResult:
            attempted: list[str] = []
            try:
                return execute_checked(attempted)
            except Exception as exc:
                raise NativeCleanupFailure(tuple(attempted)) from exc

        def execute_checked(attempted: list[str]) -> NativeCleanupResult:
            observed = rediscover_local_orphan(
                subject,
                cgroup_root=cgroup_root,
                cgroup_state_dir=cgroup_state_dir,
                network_state_dir=network_state_dir,
                ip=ip,
                nft=nft,
            )
            if observed != inventory:
                raise FleetPlanInvalid("native resource inventory drift before cleanup")
            # CO-R3U1: original journal tickets and local inode/PID observations
            # do not confer independent parent mutation or network namespace
            # custody. Reject BEFORE querying/deleting network resources or
            # touching cgroup controls. Keep original recovery records.
            require_independent_native_parent_custody(
                operation="original journal orphan teardown",
                requirement=OriginalNativeCustodyRequirement(
                    run_id=subject.run_id,
                    step_id=subject.step_id,
                    control_epoch=subject.control_epoch,
                    executor_generation=None,  # no authenticated host generation carried yet
                    attempt_identity=subject.attempt_identity,
                    parent_device=None,
                    parent_inode=None,  # no exclusive protected parent
                    resource_device=inventory.cgroup_device,
                    resource_inode=inventory.cgroup_inode,
                ),
            )
            cgroup = Path(subject.observation["cgroup_path"])
            record = _matching_record(
                cgroup_state_dir, kind="cgroup", subject=subject, resource_name=cgroup.name
            )
            # An alive predecessor must never be killed by takeover cleanup.
            # PID reuse is uncertain, NOT proof the old owner is absent.
            if _process_identity(record["pid"]) is not None:
                raise FleetPlanInvalid("predecessor PID still present or reused")
            original_identity = (inventory.cgroup_device, inventory.cgroup_inode)

            def empty() -> None:
                # Same native cgroup owner and the SAME opened directory identity
                # used by normal Fleet teardown, not pathname-based LACE guesses.
                _wait_for_cgroup_empty(
                    cgroup,
                    timeout_seconds=0.0,
                    expected_identity=original_identity,
                )

            empty()
            network_kind = subject.observation.get("network_kind")
            if network_kind == "host-netns":
                from oh_goon.integrations._fleet_plan_restricted_egress import (
                    _control_environment,
                    run_command,
                )

                namespace = subject.observation["network_namespace"]
                result = run_command(
                    ip,
                    "netns",
                    "pids",
                    namespace,
                    timeout=5.0,
                    env=_control_environment(),
                    preserve_native_executable_path=False,
                )
                if result.returncode != 0 or result.stdout.strip():
                    raise FleetPlanInvalid(
                        "native orphan network namespace has unknown descendants"
                    )
                attempted.append("NETWORK")
                _cleanup_egress_resources(
                    ip,
                    nft,
                    namespace,
                    subject.observation["host_veth"],
                    subject.observation["nft_table"],
                )
                # Even after network deletion, do not infer cgroup process absence.
                empty()
            elif network_kind != "not-allocated":
                raise FleetPlanInvalid("unknown native network mode")
            # Never recurse over unknown nested cgroups. Any nonempty descendant
            # subtree must be independently handled by the later B12E owner.
            with _bound_cgroup_directory(cgroup, original_identity) as directory:
                try:
                    with os.scandir(directory) as children:
                        if any(
                            child.is_symlink() or child.is_dir(follow_symlinks=False)
                            for child in children
                        ):
                            raise FleetPlanInvalid("native orphan has unqualified nested cgroup")
                except OSError as exc:
                    raise FleetPlanInvalid("native orphan cgroup inventory unavailable") from exc
            empty()
            attempted.append("CGROUP")
            # Only the original native owner performs deletion. This protects
            # pre-/post-cut identity and ACK, but rmdir itself is NOT an atomic
            # identity-conditional kernel operation; independent custody remains
            # a separate, blocking LACE certification requirement.
            _remove_cgroup(cgroup, expected_identity=original_identity)
            return NativeCleanupResult(tuple(attempted), "CLEANUP_ACTIONS_ATTEMPTED")

        # Original Fleet owner guarantees separate committed STARTED fact and
        # re-checks cancellation / epoch / lease immediately before this action.
        return owner.execute_native_cleanup(ticket, native_action)
