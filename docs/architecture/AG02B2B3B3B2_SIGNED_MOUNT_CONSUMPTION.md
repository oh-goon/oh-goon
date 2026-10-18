# ADR addendum — original signed mount meaning at Bubblewrap consumption

**Checkpoint:** AG-02B2B3B3B2. **Status:** hosted development, no Agent effects.

## Decision

Extend the *existing* `LocalLaceJournalReservation.reserve` and original
Bubblewrap backend. One signed `RESOURCE_INTENT.resources.mounts` digest must
be carried as part of `LocalLaceIntentProof` to the SAME Bubblewrap invocation.

- Original resource projection uses `OriginalMountIntentDescriptor` for
  existing managed input targets and host-tool snapshot identities.
- Original journal writer verifies admitted Step kind and original predecessor
  events before publishing a signed `RESOURCE_INTENT`.
- After that commit, the existing reservation owner independently verifies
  the exact signed event's resource identity and returns its mount digest.
- Before cgroup/network allocation, Bubblewrap compares the current prepared
  attempt's canonical mount plan to the committed digest, failing before effects.
- Before constructing the Bubblewrap argv, that *same* consumer compares
  again, detecting plan changes between allocation and command construction.
- The existing mandatory Agent mount guard continues to reject Agent Steps;
  **no new Agent mount grant, `RESOURCE_INTENT` writer or launcher exists**.
- The existing X0 architecture checker and its mutation tests enforce these
  original call edges. No second policy decision or execution state machine.

## Evidence strength and known limitations

The signed digest covers declared managed sandbox bindings and the original
host-tool execution snapshots. It **does not** attest to all runtime-root,
workspace, Bubblewrap implicit binds, detached Linux mounts, namespace/FD
references, consumption-time file-descriptor pinning or post-cleanup absence.
A mutable source could still change between a digest check and the kernel's
path lookup. Physical custody therefore remains unproved. Neither a good
hash match nor a successful host-private file readback permits Agent execution.

## Independent qualification ladder

1. Hosted regression: signed digest versus prepared request, source/target/
   content/tool substitution, changed request during reservation, and original
   negative Agent and signed-step-order tests.
2. Native WSL development: run `make qualify-development-wsl` against this
   **exact extracted source**, inspect `AGENT_PREWORK`, CORE,
   PHYSICAL_OBSERVER rings, `UV_LOCKED_SYNC`, `UV_ENV_VERIFY`, source PRE/POST
   manifests, process receipts and architecture result. No in-ring install.
3. Before effectful Agent Steps: original signed Agent staged-source descriptor
   must be included in `RESOURCE_INTENT` in the **same original transaction**,
   and consumed using TOCTOU-safe FD/pinned custody in the existing Bubblewrap
   builder. Independent native observation must cover all resource classes.
4. Hostile native proof: mutate a source between signing, staging, mount
   setup, execution and cleanup; test remaining mount holders, detached mounts,
   open FDs, child processes, cgroup populations and namespaces after cleanup.
   Incomplete evidence must remain UNKNOWN, never ABSENT.
5. Independently qualify Gate A issuer/roots, Gate B physical teardown per
   enabled backend, Gate C original terminal seal, AG-03/04 disclosure control,
   APP PASS and release. Green development tests never auto-promote gates.

**Invariant:** Resolve once, transport once, execute once, report once.
One canonical Runner/Enforcer/LACE/Journal owner across workload kinds.
