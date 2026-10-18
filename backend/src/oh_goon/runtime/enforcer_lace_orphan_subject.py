"""Journal-verified, epoch-fenced orphan rediscovery subject and read-only receipt."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator, canonical_bytes, digest
from oh_goon.core.ports.local_lace_orphan_inventory import LocalLaceOrphanSubject, LocalLaceOrphanInventory
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal


def subject_in(journal: CausalRunJournal, run_id: str, *, epoch: int,
               lease_identity: str, auth: ReceiptAuthenticator,
               intent_run_seq: int) -> LocalLaceOrphanSubject:
    if not journal.connection.in_transaction:
        raise CausalJournalError("original recovery subject transaction required")
    journal._check_fence(journal._row(run_id), epoch=epoch, lease_identity=lease_identity)
    events = journal.verify(run_id, auth=auth)
    intent = next((e for e in events if e["run_seq"] == intent_run_seq), None)
    if intent is None or intent["kind"] != "RESOURCE_INTENT":
        raise CausalJournalError("orphan inventory requires original authenticated RESOURCE_INTENT")
    payload = intent["payload"]
    if payload.get("backend") != "bubblewrap":
        raise CausalJournalError("orphan inventory requires local Bubblewrap authority")
    claims = [e for e in events if e["kind"] == "RESOURCE_RECOVERY_CLAIMED" and
              e["payload"].get("intent_run_seq") == intent_run_seq]
    if not claims or claims[-1]["payload"].get("control_epoch") != epoch or not claims[-1]["payload"].get("cleanup_only"):
        raise CausalJournalError("orphan inventory requires current-epoch cleanup-only claim")
    observed = [e for e in events if e["kind"] == "RESOURCE_ALLOCATION_OBSERVED" and
                e["payload"].get("intent_run_seq") == intent_run_seq]
    if len(observed) > 1:
        raise CausalJournalError("ambiguous local native allocation history")
    native = observed[0]["payload"]["observation"] if observed else None
    fields: dict[str, Any] = dict(
        run_id=run_id, intent_run_seq=intent_run_seq,
        claim_run_seq=claims[-1]["run_seq"], control_epoch=epoch,
        step_id=payload["step_id"], attempt_identity=payload["attempt_identity"],
        resources=dict(payload["resources"]), observation=dict(native) if native else None,
    )
    return LocalLaceOrphanSubject(**fields, subject_identity=digest(
        "oh-goon/b12d2b/orphan-subject/v1", canonical_bytes(fields)))


def inventory_in(journal: CausalRunJournal, subject: LocalLaceOrphanSubject,
                 inventory: LocalLaceOrphanInventory, *, epoch: int,
                 lease_identity: str, auth: ReceiptAuthenticator) -> int:
    if not journal.connection.in_transaction:
        raise CausalJournalError("original orphan inventory write transaction required")
    if type(subject) is not LocalLaceOrphanSubject or type(inventory) is not LocalLaceOrphanInventory:
        raise CausalJournalError("original native inventory contract required")
    current = subject_in(journal, subject.run_id, epoch=epoch, lease_identity=lease_identity,
                         auth=auth, intent_run_seq=subject.intent_run_seq)
    if (current != subject or inventory.subject_identity != current.subject_identity
            or inventory.state not in {"OBSERVED_PRESENT", "INTENT_ONLY_UNKNOWN"}
            or any((inventory.process_tree_proven, inventory.mounts_proven,
                    inventory.native_absence_proven, inventory.teardown_proven))):
        raise CausalJournalError("orphan inventory stale, forged or implies unproven physical safety")
    if inventory.state == "INTENT_ONLY_UNKNOWN":
        if any((inventory.cgroup_identity is not None, inventory.cgroup_device is not None,
                inventory.cgroup_inode is not None, bool(inventory.cgroup_processes),
                inventory.network_identity is not None, inventory.native_record_identity is not None)):
            raise CausalJournalError("unknown intent cannot invent a physical observation")
    else:
        if (current.observation is None or inventory.cgroup_identity != current.observation.get("cgroup_identity")
                or inventory.network_identity != (current.observation.get("network_identity")
                    if current.observation.get("network_kind") == "host-netns" else None)
                or type(inventory.cgroup_device) is not int or inventory.cgroup_device < 1
                or type(inventory.cgroup_inode) is not int or inventory.cgroup_inode < 1
                or type(inventory.native_record_identity) is not str
                or not inventory.native_record_identity.startswith("sha256:")
                or len(inventory.native_record_identity) != 71
                or any(type(item) is not tuple or len(item) != 4
                       or type(item[0]) is not int or item[0] < 1
                       or any(type(v) is not str or not v for v in item[1:])
                       for item in inventory.cgroup_processes)):
            raise CausalJournalError("physical inventory differs from original allocation witness")
    events = journal.verify(subject.run_id, auth=auth)
    if any(e["kind"] == "RESOURCE_ORPHAN_INVENTORIED" and
           e["payload"].get("claim_run_seq") == subject.claim_run_seq for e in events):
        raise CausalJournalError("orphan inventory already committed under this recovery claim")
    return journal._append_locked(subject.run_id, "RESOURCE_ORPHAN_INVENTORIED", {
        "intent_run_seq": subject.intent_run_seq, "claim_run_seq": subject.claim_run_seq,
        "control_epoch": epoch, "subject_identity": subject.subject_identity,
        "inventory": {**asdict(inventory), "cgroup_processes": [list(p) for p in inventory.cgroup_processes]},
        "stage": "READ_ONLY_UNQUALIFIED",
        "cleanup_permitted": False, "teardown_proven": False,
    })
