# ADR — B27 original mount/FD snapshot correspondence (P1B27B2B3B2A)

Status: **implemented for hosted development observation, NOT a physical Gate B pass**.
Parent: exact P1B27B2B3B1A. Date: 2026-10-14.

## Decision

Keep the existing Resolver → original Enforcer RESOURCE_INTENT → one original
Fleet/LACE execution and recovery owner → one Linux physical reader →
read-only Game Tape projection. Do not introduce an Agent runtime, second
kernel scanner, signer, authority registry, scheduler or physical-fact publisher.

The original tracked-mount namespace and visible FD census already carry the
kernel mount namespace handle and the complete signed-target mount-ID/device/root
fingerprint. Their independently stable snapshots are NOT automatically the
same physical fact. The original shared Linux `observe_kernel` double-scan now
compares both identities *after* its two reads, before forming the one immutable
`PhysicalKernelSnapshot`. Disagreement, missing fields, incomplete coverage,
foreign namespace or a contradictory state becomes `UNKNOWN` for both reports.
`NOT_OBSERVED` continues to mean narrowly scoped visible nondetection.

No cross-probe agreement is accepted as proof that Bubblewrap consumed the
original sealed FD, that no mount survived, that all PID namespaces were
observed, that kernel handles are gone, or that an independent physical
custodian exists. A matching fingerprint is correlation, not provenance or
physical custody. Every resulting fact remains unqualified. No production
`PHYSICAL_FACT`, `QUIESCENCE_PROVEN`, `TEARDOWN_PROVEN`, Gate B or release
transition is introduced.

## Authority ownership and contract

- Original scope/meaning owner: signed `RESOURCE_INTENT` and original immutable
  `OriginalRunPhysicalEnvelope`; admission and reservations remain unchanged.
- Original effect owner: existing Bubblewrap backend and original LACE lease.
- Observation owner: existing `linux_physical_observer.observe_kernel`, no
  alternative call site, no journal mutation or diagnostic promotion.
- Inputs: original typed scope and the existing two kernel probes.
- Output: read-only `PhysicalKernelSnapshot` with existing state/detail schema.
- Fail-closed behavior: kernel identity mismatches or under-specified identities
  cannot be reported as corresponding physical holder evidence.
- Negative evidence: a missing FD remains `NOT_OBSERVED`, NEVER absence.
- Validation: synthetic `/proc` fixtures, adversarial identity substitution,
  cross-read races, X0 structural guard and normal QENV1 PHYSICAL_OBSERVER ring.

## Independent blocking evidence for P1B27B2B3B2B

On a Linux/WSL host with FD-native Bubblewrap support, run an effectful original
signed FD-bind fixture through the one existing Fleet/LACE path; record exact
kernel mount IDs, device/root identities, namespace and process holder roster
while active, during crash/cancel and after teardown. Use the original
authenticated sample and independently held observer custody. Include stacked
mount, FD replacement, source mutation, PID namespace changes, orphaned FDs,
mount namespace holder survives, cgroup/process survives, and blocked teardown.
Only a separately reviewed physical authority may establish bounded real
absence and classify teardown, after external T0B2 custodian proof and Gate A.
No hosted synthetic PASS or historic exact-source native PASS transfers.
