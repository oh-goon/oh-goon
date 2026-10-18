# ADR CO-R3U1 — Original LACE native-effect gate before destructive work

**Decision (2026-10-15):** Development fail-closed implementation. Host-provisioned exclusive kernel parent mutation custody **NOT PROVEN**. CO-R3U remains open and no physical Gate B, real-native PASS, signer Gate A or release is asserted.

## Security claim we will *not* make

A journal ticket, signed RESOURCE_INTENT, locally pinned device/inode, process-death observation, Python object, OS-file lock, local database row and `rmdir(name, dir_fd=...)` do not prove an independently controlled, exclusive cgroup parent. An attacker able to rename the original cgroup between the final `stat` and the destructive syscall can cause deletion of an unrelated entry; later link-count checks only detect the damage. A deleted namespace name similarly does not prove all foreign holders are gone.

## Frozen implementation boundary

* Existing native effect owner: `integrations/_fleet_plan_cgroup_resources.py`. `require_independent_native_parent_custody()` is one **non-issuing** security gate. `CgroupV2AttemptLease.create()` refuses after validating the real delegated cgroup-v2 host and **before** native allocation/record/mutable controller effects. Genuine cgroup2 delegated child and control writes, `close`, `kill` and `remove` refuse before destructive calls. Fake fixture cgroup directories remain ordinary developmental unit tests, not OS custody evidence.
* Original journal recovery owner: `integrations/fleet_plan_local_lace_native_reaper.py`, under its existing Enforcer ticket and mutation cut. Builds a typed requirement and calls the same original native gate **before** namespace/network deletion, cgroup kill/remove, or any cleanup-attempt label. The durable original recovery record remains; the existing journal owner will record failure, never absence. No extra recovery actor or gate signer was created.
* Immutable transport: `core/models/original_native_custody.py` holds exact Run/Step/epoch/attempt, executor generation and parent/resource dev+inode **requirements**. Its instances and `complete_scope` are *never* accepted as permits. Current original journal recovery lacks host executor generation and parent custody, explicitly represented as `None`.
* The existing X0 single-path architecture checker contains a focused mutation guard so the original early gate cannot silently be removed. Focused adversarial tests enforce that forged complete scopes do not authorize work, that native callbacks/syscalls are not reached and that records survive.

## Remaining CO-R3U, blocked on actual independently delegated Linux host

1. Provision one original LACE native custodian with a real OS-enforced exclusive parent mutation boundary and its own trust/root separation from the workload; no Python, PID or file-lock self-assertion, no new runtime issuer, no duplicate execution path.
2. Verify an authenticated current Run/Step trust epoch and executor generation at the **native effect cut**. Bind resource + parent kernel objects in one immutable scope and preserve revocation without switching backends.
3. Prove namespace, cgroup and parent cannot be replaced by foreign writers at syscall time; otherwise refuse identity-nonconditional `rmdir` and quarantined teardown. Keep actual reaper and cgroup owner as sole effect owners.
4. Add hostile real-host tests for parent rename/replacement, reused FD, namespace holder and mounts, stale and revoked epoch, crash/restart, EIO/ENOSPC and actual effect-time control. Re-run exact-source native WSL; record BLOCKED rather than fabricate proof.
5. Complete normal active network-only and failure-rollback effects under the same original LACE physical authority, and do not permit writer-bearing Git retirement before Gate B.

**Hashmarks:** No new dependency, integration, diagnostic method or Hashmarks-owned runtime evidence intelligence is included. No production owner may use a Hashmarks observation as cleanup permission.
