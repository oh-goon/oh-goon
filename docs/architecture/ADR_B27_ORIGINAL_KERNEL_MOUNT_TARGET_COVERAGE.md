# ADR: Original LACE kernel mount-target coverage (P1B27B2B3A)

**Status:** accepted for development observation only; not a Gate B, production
containment, physical-absence or signer-custody certification.

## Problem

The existing read-only Linux physical observer returned `PRESENT` from a tracked
mount namespace if **any** signed-declared target appeared in mountinfo. A
partial collection (one of multiple required targets), or multiple stacked
mount records for the same declared target, could thus appear indistinguishable
from a complete visible target collection.

## Decision

Extend the **existing** `_tracked_namespace_inventory` in
`integrations/linux_physical_observer.py`. It now collects kernel mount IDs,
parent IDs, filesystem major:minor, source roots and mount points in its
single existing bounded `/proc/<tracked-pid>/mountinfo` scan. It matches every
declared original mount target, emits a deterministic hash of the observed
kernel target records, and distinguishes:

- `PRESENT`: each declared target has exactly one visible mount record.
- `UNKNOWN`: some (but not all) targets are visible, records are stacked at a
  target, process/namespace identity races, malformed mountinfo, or missing
  kernel source identity fields.
- `NOT_OBSERVED`: none of the declared targets are visible in this **specific**
  pinned process mount namespace.

`PRESENT` is scoped visibility, **not** a validated equality between the
kernel's source object and the source FD pinned by Bubblewrap. `NOT_OBSERVED`
is **never** proof of global absence: detached mounts, hidden PID namespaces,
foreign namespaces, kernel-held references, and post-teardown lifecycle remain
unproven. The signed Run/Step allocation authority, physical-fact writer,
Enforcer classifier and Game Tape are unchanged.

## Verification and anti-drift

- Existing two-scan `observe_kernel` compares complete result identity and
  downgrades raced observation to `UNKNOWN`.
- The established X0 checker requires a single shared owner, incomplete and
  stacked target rejection, target identity digest and explicit uncertainty.
- Focused adversarial tests cover partial/none/all targets, stacked targets,
  duplicate IDs, malformed device numbers and mount paths, two-scan races and
  mutations to the protections.
- The normal `AGENT_PREWORK` and `PHYSICAL_OBSERVER` QENV1 rings select the
  tests on the P1B27B2B3A exact source manifest.

## Outstanding

Native effectful FD-bind mount confirmation, independently trusted allocation
witness, exact FD-to-mounted-object provenance, original source content
immutability, all-namespace holder census, cleanup/crash recovery,
post-teardown physical absence and Enforcer's single authenticated fact path.
Never auto-promote this diagnostic result into proof or start Agent effects.
