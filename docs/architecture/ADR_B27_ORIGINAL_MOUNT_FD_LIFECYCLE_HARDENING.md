# ADR — B27 original LACE mount/FD lifecycle observation hardening

Status: **Implemented development-only** (P1B27B2B3B1, 2026-10-14). Not Gate B or effect authorization.

## Problem

The single LACE Linux kernel observer already checked full signed mount targets in
`_tracked_namespace_inventory`, but its adjacent `visible_mount_fd_holders`
probe could count a mount ID while the full signed roster was partial or
stacked, or while the mount table/FD table changed within its own census.
An apparently stable FD hint was insufficient for original resource custody.

## Decision and owner

The **existing** `linux_physical_observer._visible_mount_fd_holders` owns this
read-only extension. Its original caller is `observe_kernel`; its original Run
recovery owner, envelope, journal and Enforcer outcome owner are unchanged.
It now requires every signed declared target to appear exactly once, validates
kernel device and decoded mount source root, and computes the same target
identity digest as `_tracked_namespace_inventory`. It rejects per-census FD
roster and mountinfo changes and continues to rely on `observe_kernel`'s
second-scan consistency check. Unknown or partial state always means `UNKNOWN`.
No new publisher, effect permission, trust root, signer, agent runtime or
observer was created.

## Boundaries and unfinished proof

Present means visible local matches in the tracked mount namespace, **not**
verified Bubblewrap FD consumption, exclusive source-object custody or
physical containment. NOT_OBSERVED means local non-detection only. No local
`/proc` observer can prove detached mounts, hidden PID namespaces, all kernel
references, independently protected observer custody or teardown absence.

Next: native effectful FD-bind mount-to-kernel identity capture and surviving
namespace/FD-holder test under the existing signed original Run resource
identity, independent physical facts, bounded crash/teardown convergence and
Gate B native verification. Gate A, AG-03/04, Gate C and release remain blocked.

## Negative contract

Adversarial synthetic `/proc` tests reject missing/partial/stacked target
coverage, device/escaped source errors, FD list races and mount table changes.
X0 verifies the existing probe keeps its guards and digest. QENV1 enrolls the
new tests in the original qualification rings without package-manager work
inside any ring.
