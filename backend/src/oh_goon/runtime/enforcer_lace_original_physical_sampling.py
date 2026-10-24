"""B12E-C2 development slice: original LACE owner reads ONE shared Linux observer.

This is NOT the B12E independent physical-fact publisher. Original signer/root
provisioning, complete resource provenance and physical closure remain blocked.
No operator-selected diagnostic CLI is used by Fleet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator, canonical_bytes, digest
from oh_goon.core.ports.original_mount_intent import OriginalMountIntentDescriptor
from oh_goon.core.ports.physical_observation import (
    ORIGINAL_KERNEL_COMPONENTS,
    OriginalRunPhysicalEnvelope,
    PhysicalKernelSnapshot,
    PhysicalObservationScope,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal
from oh_goon.runtime.enforcer_lace_orphan_subject import subject_in
from oh_goon.runtime.enforcer_lace_takeover_preflight import require_original_takeover_lineage_in


@dataclass(frozen=True, slots=True)
class OriginalPhysicalSamplingBinding:
    """Authenticated journal lineage, not authority to publish physical facts."""

    run_id: str
    intent_run_seq: int
    claim_run_seq: int
    control_epoch: int
    subject_identity: str
    head_identity: str
    scope: PhysicalObservationScope
    mount_intent_identity: str
    envelope: OriginalRunPhysicalEnvelope


def require_original_physical_sampling_in(
    journal: CausalRunJournal,
    *,
    run_id: str,
    intent_run_seq: int,
    epoch: int,
    lease_identity: str,
    auth: ReceiptAuthenticator,
    executor_generation: int | None,
    issuer_key_id: str | None,
    scope: PhysicalObservationScope,
    mount_intent: OriginalMountIntentDescriptor,
) -> OriginalPhysicalSamplingBinding:
    """Fail before kernel reading unless the original signed scope is exact.

    The journal does not yet authenticate *mount targets* or an independent
    observer custodian: they are sampled but NEVER given fact-publishing authority.
    """
    if not journal.connection.in_transaction:
        raise CausalJournalError("original Fleet sampling transaction required")
    if (
        type(scope) is not PhysicalObservationScope
        or type(mount_intent) is not OriginalMountIntentDescriptor
    ):
        raise CausalJournalError("typed original physical sampling scope required")
    require_original_takeover_lineage_in(
        journal,
        run_id=run_id,
        intent_run_seq=intent_run_seq,
        epoch=epoch,
        lease_identity=lease_identity,
        auth=auth,
        admitted_executor_generation=executor_generation,
        admitted_issuer_key_id=issuer_key_id,
    )
    original = [
        event
        for event in journal.verify(run_id, auth=auth)
        if event["run_seq"] == intent_run_seq and event["kind"] == "RESOURCE_INTENT"
    ]
    if (
        len(original) != 1
        or original[0]["payload"]["resources"]["mounts"] != mount_intent.resource_identity
        or scope.mount_targets != mount_intent.binding_targets
    ):
        raise CausalJournalError("mount targets not bound to original signed RESOURCE_INTENT")
    subject = subject_in(
        journal,
        run_id,
        epoch=epoch,
        lease_identity=lease_identity,
        auth=auth,
        intent_run_seq=intent_run_seq,
    )
    events = journal.verify(run_id, auth=auth)
    inventories = [
        e
        for e in events
        if e["kind"] == "RESOURCE_ORPHAN_INVENTORIED"
        and e["payload"].get("claim_run_seq") == subject.claim_run_seq
    ]
    if len(inventories) != 1:
        raise CausalJournalError("exact original native orphan inventory required")
    inventory = inventories[0]["payload"]["inventory"]
    allocated = subject.observation
    if (
        type(allocated) is not dict
        or inventory.get("state") != "OBSERVED_PRESENT"
        or inventory.get("subject_identity") != subject.subject_identity
        or inventories[0]["payload"].get("intent_run_seq") != intent_run_seq
        or inventories[0]["payload"].get("control_epoch") != epoch
        or inventories[0]["payload"].get("subject_identity") != subject.subject_identity
        or inventory.get("cgroup_identity") != allocated.get("cgroup_identity")
        or inventory.get("network_identity")
        != (
            allocated.get("network_identity")
            if allocated.get("network_kind") == "host-netns"
            else None
        )
        or any(
            inventory.get(flag) is not False
            for flag in (
                "process_tree_proven",
                "mounts_proven",
                "native_absence_proven",
                "teardown_proven",
            )
        )
        or type(inventory.get("native_record_identity")) is not str
        or scope.run_id != run_id
        or scope.subject_identity != subject.subject_identity
        or scope.cgroup_path != allocated.get("cgroup_path")
        or scope.cgroup_device != inventory.get("cgroup_device")
        or scope.cgroup_inode != inventory.get("cgroup_inode")
        or type(scope.mount_targets) is not tuple
        or not scope.mount_targets
        or len(scope.mount_targets) > 64
        or any(
            type(target) is not str or not target.startswith("/") or ".." in target.split("/")
            for target in scope.mount_targets
        )
        or (scope.network_namespace, scope.host_veth, scope.nft_table)
        != (
            allocated.get("network_namespace"),
            allocated.get("host_veth"),
            allocated.get("nft_table"),
        )
        or not any(
            type(proc) is list
            and len(proc) == 4
            and proc[0] == scope.tracked_pid
            and proc[1] == scope.tracked_birth
            for proc in inventory.get("cgroup_processes", [])
        )
    ):
        raise CausalJournalError("sample scope differs from original allocation/inventory")
    # Reconcile the actual original orphan inventory roster, not an untrusted
    # scope-only PID. No additional /proc read or alternate native inventory.
    # Duplicates, altered mountinfo hashes, mixed boots or ambiguous identities
    # reject before invoking the single shared Linux scanner.
    roster = inventory.get("cgroup_processes")
    if type(roster) is not list or not roster or len(roster) > 4096:
        raise CausalJournalError("original native process inventory incomplete or unbounded")
    try:
        rows = tuple(tuple(row) for row in roster)
        # Verified in OriginalRunPhysicalEnvelope's immutable constructor below.
        native_processes = rows
    except (TypeError, ValueError) as exc:
        raise CausalJournalError("malformed original native process inventory") from exc
    # No additional authority: exact identities of three ORIGINAL signed event
    # states are transported once. Missing native mount/fd/observer custody is
    # made explicit by the immutable envelope instead of inferred from a scope.
    try:
        envelope = OriginalRunPhysicalEnvelope(
            run_id=run_id,
            intent_run_seq=intent_run_seq,
            claim_run_seq=subject.claim_run_seq,
            control_epoch=epoch,
            subject_identity=subject.subject_identity,
            signed_intent_identity=digest(
                "oh-goon/b12e/signed-intent-event/v1", canonical_bytes(original[0])
            ),
            allocation_identity=digest(
                "oh-goon/b12e/allocation-observation/v1", canonical_bytes(allocated)
            ),
            inventory_identity=digest(
                "oh-goon/b12e/original-inventory/v1", canonical_bytes(inventories[0])
            ),
            native_record_identity=inventory["native_record_identity"],
            mount_intent_identity=mount_intent.resource_identity,
            declared_mount_targets=mount_intent.binding_targets,
            native_cgroup_device=inventory["cgroup_device"],
            native_cgroup_inode=inventory["cgroup_inode"],
            observed_processes=native_processes,
        )
    except ValueError as exc:
        raise CausalJournalError("original native resource roster fails typed validation") from exc
    if not any(
        row[0] == scope.tracked_pid and row[1] == scope.tracked_birth
        for row in envelope.observed_processes
    ):
        raise CausalJournalError("tracked native process not in original resource inventory")
    if any(
        row[1].rsplit(":", 1)[0] != scope.tracked_birth.rsplit(":", 1)[0]
        for row in envelope.observed_processes
    ):
        raise CausalJournalError("original inventory mixes process boot identities")
    # Full verified journal head is part of the two-cut race fence. No writes.
    return OriginalPhysicalSamplingBinding(
        run_id,
        intent_run_seq,
        subject.claim_run_seq,
        epoch,
        subject.subject_identity,
        journal._row(run_id)[4],
        scope,
        mount_intent.resource_identity,
        envelope,
    )


def require_original_sample_kernel_identity(
    binding: OriginalPhysicalSamplingBinding,
    facts: PhysicalKernelSnapshot,
) -> None:
    """Check the shared reader's positive namespace/mount claims against the signed roster.

    This is a non-authorizing, read-only consistency fence. It does NOT
    establish complete native resources, source-FD-to-mount provenance,
    observer custody, disappearance or Gate B. Incomplete evidence stays
    unqualified; contradictory positive claims cannot leave the original owner.
    """
    if (
        type(binding) is not OriginalPhysicalSamplingBinding
        or type(facts) is not PhysicalKernelSnapshot
    ):
        raise CausalJournalError(
            "typed original sampling binding and shared kernel snapshot required"
        )
    observed = facts.to_mapping()
    tracked = observed.get("tracked_mount_namespace")
    holders = observed.get("visible_mount_fd_holders")
    # A positive FD-holder claim without the same complete original mount
    # namespace has no referent.  The shared observer normally correlates
    # these probes, but the authoritative ORIGINAL journal owner must refuse
    # externally supplied/malformed positive snapshots as well.
    if (
        holders is not None
        and holders["state"] == "PRESENT"
        and (tracked is None or tracked["state"] != "PRESENT")
    ):
        raise CausalJournalError("positive mount FD holders lack original visible mount namespace")
    if tracked is None or tracked["state"] != "PRESENT":
        return  # Unknown and scoped nondetection cannot become absence proof.

    claimed_namespace = re.search(
        r"(?:^|; )tracked process mount namespace=(mnt:\[[1-9][0-9]*\])(?:;|$)",
        tracked["detail"],
    )
    source = next(
        (
            row
            for row in binding.envelope.observed_processes
            if row[0] == binding.scope.tracked_pid and row[1] == binding.scope.tracked_birth
        ),
        None,
    )
    if claimed_namespace is None or source is None or claimed_namespace.group(1) != source[2]:
        raise CausalJournalError("visible mount namespace disagrees with original native inventory")

    # The shared Linux scanner promises full exact-target coverage when it
    # reports PRESENT. The original journal owner must independently refuse
    # a fabricated/malformed positive claim, not trust a detail-only digest
    # that can correspond to an incomplete or stacked target inventory.
    expected = len(binding.envelope.declared_mount_targets)
    detail = tracked["detail"]
    coverage_fields = re.findall(r"(?:^|; )visible_targets=([0-9]+)/([0-9]+)(?=;|$)", detail)
    ambiguity_fields = re.findall(r"(?:^|; )ambiguous_target_mounts=(True|False)(?=;|$)", detail)
    id_fields = re.findall(r"(?:^|; )target_mount_ids=\[([0-9, ]*)\](?=;|$)", detail)
    if any(len(rows) != 1 for rows in (coverage_fields, ambiguity_fields, id_fields)):
        raise CausalJournalError("visible mount target ledger incomplete or ambiguous")
    coverage = coverage_fields[0]
    mount_ids = id_fields[0].split(", ") if id_fields[0] else []
    if (
        int(coverage[0]) != expected
        or int(coverage[1]) != expected
        or ambiguity_fields[0] != "False"
        or len(mount_ids) != expected
        or any(not value.isdecimal() or int(value) < 1 for value in mount_ids)
        or len(set(mount_ids)) != expected
    ):
        raise CausalJournalError("visible mount target ledger differs from original signed scope")

    if holders is None or holders["state"] == "UNKNOWN":
        return

    # Require exactly ONE declaration per field.  First-match regex searches
    # would admit duplicate contradictory values into the original journal.
    def field(detail: str, name: str, pattern: str) -> str:
        found = re.findall(r"(?:^|; )" + re.escape(name) + r"=(" + pattern + r")(?=;|$)", detail)
        if len(found) != 1:
            raise CausalJournalError(
                "visible mount/FD identity field missing or duplicated: " + name
            )
        return str(found[0])

    kernel_target = field(tracked["detail"], "target_kernel_sha256", r"[0-9a-f]{64}")
    holder_target = field(holders["detail"], "target_kernel_sha256", r"[0-9a-f]{64}")
    holder_namespace = field(holders["detail"], "original_namespace", r"mnt:\[[1-9][0-9]*\]")
    if kernel_target != holder_target or holder_namespace != claimed_namespace.group(1):
        raise CausalJournalError("visible mount/FD claims disagree with original tracked namespace")

    if holders["state"] == "PRESENT":
        # A positive FD holder must actually have seen matching kernel FDs.
        # These are still visible-PID counters, NEVER full host-wide custody.
        census = holders["detail"]
        count = {
            name: int(field(census, name, r"[0-9]+"))
            for name in ("visible_pids", "target_mount_ids", "inspected_fds", "matching_mount_fds")
        }
        field(census, "inventory_sha256", r"[0-9a-f]{64}")
        if (
            count["visible_pids"] < 1
            or count["target_mount_ids"] != expected
            or count["matching_mount_fds"] < 1
            or count["matching_mount_fds"] > count["inspected_fds"]
        ):
            raise CausalJournalError(
                "positive visible mount FD census cannot support claimed holders"
            )


def original_sample_position(
    events: Sequence[dict[str, Any]],
    *,
    claim_run_seq: int,
    intent_run_seq: int,
    subject_identity: str,
) -> tuple[int, int | None, int | None, int | None]:
    """One canonical, non-authorizing timeline of the current recovery claim.

    Used by BOTH the Fleet write cuts and original journal verification. The
    native cleanup STARTED receipt is not success; RECORDed is not absence.
    Previous snapshots may be byte-identical: their *separate* kernel reads
    are ordered by the original journal, never misrepresented as independent.
    """
    samples = [
        e
        for e in events
        if e["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
        and e["payload"].get("claim_run_seq") == claim_run_seq
        and e["payload"].get("intent_run_seq") == intent_run_seq
        and e["payload"].get("subject_identity") == subject_identity
    ]
    if len(samples) >= 256:
        raise CausalJournalError("bounded original physical sampling rounds exhausted")
    previous = samples[-1]["run_seq"] if samples else None
    preparations = {
        e["run_seq"]: e["payload"]
        for e in events
        if e["kind"] == "RESOURCE_CLEANUP_PREPARED"
        and e["payload"].get("claim_run_seq") == claim_run_seq
        and e["payload"].get("intent_run_seq") == intent_run_seq
        and e["payload"].get("subject_identity") == subject_identity
    }
    starts = [
        e
        for e in events
        if e["kind"] == "RESOURCE_CLEANUP_NATIVE_STARTED"
        and e["payload"].get("prepare_run_seq") in preparations
        and e["payload"].get("ticket_identity")
        == preparations[e["payload"]["prepare_run_seq"]].get("ticket_identity")
    ]
    start = starts[-1]["run_seq"] if starts else None
    recorded = (
        [
            e
            for e in events
            if e["kind"] == "RESOURCE_CLEANUP_NATIVE_RECORDED"
            and e["payload"].get("started_run_seq") == start
        ]
        if start is not None
        else []
    )
    if len(recorded) > 1:
        raise CausalJournalError("ambiguous original native cleanup record")
    return len(samples) + 1, previous, start, recorded[0]["run_seq"] if recorded else None


@dataclass(frozen=True, slots=True)
class OriginalPhysicalConvergenceAssessment:
    """Original-owner visibility assessment, NEVER physical-absence authority."""

    state: str
    post_cleanup_rounds: int
    cleanup_started_run_seq: int | None
    cleanup_recorded_run_seq: int | None
    last_sample_run_seq: int | None
    observer_custody: str = "UNVERIFIED"
    coverage: str = "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
    physical_absence_proven: bool = False
    quiescence_proven: bool = False
    teardown_proven: bool = False
    effect_permission: str = "NONE"


def assess_original_physical_convergence_unqualified(
    events: tuple[dict[str, Any], ...],
    *,
    binding: OriginalPhysicalSamplingBinding,
) -> OriginalPhysicalConvergenceAssessment:
    """Read verified original journal rounds; never turn nondetection into absence.

    This assessment neither invokes /proc nor writes a journal entry. The
    original Fleet owner supplies the same issuer/lease/claim-bound binding
    required for recording samples. In particular, identical local scans
    cannot establish independent witness custody or full resource coverage.
    """
    if type(binding) is not OriginalPhysicalSamplingBinding or type(events) is not tuple:
        raise CausalJournalError("original verified claim and journal required")
    _, _, cleanup_started, cleanup_recorded = original_sample_position(
        list(events),
        claim_run_seq=binding.claim_run_seq,
        intent_run_seq=binding.intent_run_seq,
        subject_identity=binding.subject_identity,
    )
    samples = [
        e
        for e in events
        if e["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
        and e["payload"].get("claim_run_seq") == binding.claim_run_seq
        and e["payload"].get("intent_run_seq") == binding.intent_run_seq
        and e["payload"].get("subject_identity") == binding.subject_identity
    ]
    last_sample_seq = samples[-1]["run_seq"] if samples else None

    def result(state: str, number: int = 0) -> OriginalPhysicalConvergenceAssessment:
        return OriginalPhysicalConvergenceAssessment(
            state=state,
            post_cleanup_rounds=number,
            cleanup_started_run_seq=cleanup_started,
            cleanup_recorded_run_seq=cleanup_recorded,
            last_sample_run_seq=last_sample_seq,
        )

    if cleanup_started is None:
        return result("CLEANUP_NOT_STARTED")
    if cleanup_recorded is None:
        return result("CLEANUP_INTERRUPTED")

    post = [event for event in samples if event["run_seq"] > cleanup_recorded]
    if not post:
        return result("NO_POST_CLEANUP_OBSERVATION")
    # Every accepted round must refer to the SAME authenticated scope and
    # original native inventory. Never join foreign scopes or stale cleanup.
    if any(
        event["payload"].get("schema") != "oh-goon/original-unqualified-physical-sample/v2"
        or event["payload"].get("cleanup_started_run_seq") != cleanup_started
        or event["payload"].get("cleanup_recorded_run_seq") != cleanup_recorded
        or event["payload"].get("envelope_identity") != binding.envelope.identity
        or event["payload"].get("mount_intent_identity") != binding.mount_intent_identity
        or event["payload"].get("scope")
        != {
            **asdict(binding.scope),
            "mount_targets": list(binding.scope.mount_targets),
        }
        for event in post
    ):
        return result("INCOMPLETE_OR_CHANGED_SCOPE", len(post))
    # A v2 sample can be a valid bounded, hash-chained partial diagnostic.
    # But it cannot be assessed as a convergence window unless *every* round
    # carries the exact roster of the original shared Linux double-scan.
    # No second scanner and no inference that even a complete local /proc
    # view proves detached/foreign resource absence.
    if any(
        tuple(fact["component"] for fact in event["payload"]["observed"])
        != ORIGINAL_KERNEL_COMPONENTS
        for event in post
    ):
        return result("INCOMPLETE_OR_CHANGED_SCOPE", len(post))
    observed = [fact for event in post for fact in event["payload"]["observed"]]
    if any(fact["state"] == "PRESENT" for fact in observed):
        return result("VISIBLE_RESOURCES_REMAIN", len(post))
    if any(fact["state"] == "UNKNOWN" for fact in observed):
        return result("INCOMPLETE_OR_CHANGED_SCOPE", len(post))
    if len(post) < 2:
        return result("INSUFFICIENT_POST_CLEANUP_ROUNDS", len(post))
    # NOT_OBSERVED is only local scoped nondetection: never a closure PASS.
    return result("REPEATED_SCOPED_NONDETECTION", len(post))
