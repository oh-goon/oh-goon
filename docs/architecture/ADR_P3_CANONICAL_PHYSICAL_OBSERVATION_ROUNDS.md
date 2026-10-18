# ADR — P3A original physical-observation rounds and cleanup ancestry

Status: IMPLEMENTED DEVELOPMENT ONLY; **not** physical Gate B acceptance.

## Context

P1P2B2B3 commits one explicitly unqualified sample under the original Fleet
journal. The previous replay check rejected **any** later sample with identical
kernel content, blocking the multiple real observation windows required for
bounded teardown convergence. An unchanged snapshot is not evidence of an
unchanged *world* or independent custody.

## Decision

Keep one shared Linux observer, one original Fleet sampling owner, the two
lease/claim/journal validation cuts and exactly one original journal writer.
Allow repeat readings to be ordered as authenticated **journal entries**:

- `sample_round` increments within the current original Run/claim/intent/subject;
  `previous_sample_run_seq` binds the preceding sample's journal sequence.
- Link to the latest same-claim original native cleanup STARTED and RECORDED
  events, or explicitly `null` if not present. STARTED without RECORDED remains
  unresolved. RECORDED success only means actions were attempted.
- One shared `original_sample_position()` function determines these fields both
  at Fleet append and in journal replay validation. Rehashed attacks changing
  round, predecessor or cleanup ancestry are rejected.
- `v2` is the new sample schema; historical `v1` journal events remain readable
  with their original stricter replay rule. At most 256 rounds per claim.
- All samples remain `UNVERIFIED`, `INCOMPLETE_NATIVE_RESOURCE_SCOPE`,
  `effect_permission=NONE`, with physical absence, quiescence and teardown false.

## Boundaries

There is **no** extra observer, signer, trust root, publisher, runtime, release
classifier, sampling scheduler or mutation permission. Journal chain integrity
is not independent kernel-observer custody. No decision about physical absence
can be made from repeated `/proc` nondetection.

## Next verification

Real native effectful mount allocation, cleanup interruption, cancellation,
revocation, surviving FD/namespace holders and bounded independent postcleanup
scans must be qualified on the **exact** source. P3B may consume these recorded
rounds only after independent custody and full resource-scope completeness.
