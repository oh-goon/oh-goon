# ADR — CO-R3M: durable pending allocation before native cgroup mkdir

Status: **development implementation, not release**. Parent: CO-R3L exact source
`6010de36de64d2ef7c99c4adc8c55d02b9b4c439587b79e712644eeea2be5842`.

## Context and defect

The original `CgroupV2AttemptLease.open()` allocated the kernel cgroup via
`mkdir` and only afterward observed its device/inode and published its lease.
A process killed in that interval could leave a physical cgroup without any
recovery record, and PID/name-based cleanup cannot safely reconstruct which
kernel inode belongs to the terminated attempt. Simply retrying the pathname
would authorize destructive effects on a replacement.

## Decision

Retain one original native owner, original allocation lock, original
`publish_text_file` durability semantics, and original state-file directory.
Before `mkdir`, atomically and durably publish **phase=allocation_pending**
with process boot/start identity, target basename and any original LACE intent,
but *no fabricated cgroup device/inode*. Reject an existing state file before
overwrite. The first kernel-effect call may occur only after pending publication
succeeds. On successful creation and native identity capture, the same owner
publishes **phase=identity_bound** including device/inode before controller
writes or process attachment.

On failed/uncertain `mkdir` or inaccessible original identity, retain the
pending record. Do not kill, remove, or silently retire a possibly created
resource. An expired/dead pending record is an **unresolved allocation** and
stops the existing original orphan-reconciler before any native destructive
operation, even if the target pathname is absent. The original legacy pinned
records remain supported as `identity_bound` for compatibility; unknown phases
are invalid. Authenticated LACE-owned records retain original journal recovery.

This deliberately chooses a fail-closed availability cost: no automated
reconciliation may invent original cgroup identity. Only an externally proven
physical custody/recovery procedure can resolve such a record, outside this
checkpoint's authority. No second registry, admission runtime or reaper.

## Proof and limitations

Native subprocess `os._exit` immediately after `mkdir` exercises the original
`open()` path; the persisted pending record survives child death, and stale
reconciliation performs no unbound kill or removal. Additional adversarial tests
cover lost mkdir acknowledgment, write failure before effects, identity probe
failure, foreign path substitution, stale pending with absent target, existing
record overwrite, invalid phase, and original owner architecture regressions.

Hosted temporary filesystem effects cannot certify native cgroup-v2 crash
persistence after power loss, directory fsync ordering on a target filesystem,
real delegated cgroup-controller failures, LACE escaped writer absence, or
independently enforced parent mutation custody. The Linux `rmdir` syscall is
still not identity-conditional; no release or writer-bearing Git deletion
authority is acquired here.

## Required next proof

Run `make qualify-development-wsl QUALIFY_EVIDENCE_DIR=../OH_GOON_CO_R3M_NATIVE_EVIDENCE`
on a fresh extracted exact source. Separately test real delegated cgroup-v2
process attachment/termination and crash cuts with independently provisioned
Enforcer/LACE custody and negative physical-survivor controls. Gate A/B/C,
P3C, CO-R2b, APP PASS and release remain blocked.
