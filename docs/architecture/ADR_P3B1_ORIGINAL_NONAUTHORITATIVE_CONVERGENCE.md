# ADR P3B1 — Original post-cleanup visibility assessment

Status: Accepted for development, **not certified**. Parent: exact P3A.

## Decision

Retain exactly one Fleet recovery owner, one native cleanup chain, one shared
read-only Linux observer, and one original Run journal. The owner may produce a
**read-only, non-authorizing** interpretation of its already journal-verified
P3A sampling rounds. Do not add another poller, native scanner, publication
writer, trust signer, final classifier, or terminal state transition.

The original Fleet owner validates its session/lease, provisioned generation
and issuer, signed intent, recovery claim, original inventory and mount scope
before reading verified original Run journal events. The bounded original
`original_sample_position` determines the latest cleanup STARTED/RECORDED
ancestry. Pre-cleanup samples are never counted toward post-cleanup
visibility. All qualifying rounds must come from that cleanup lineage and
share the same envelope, mount intent, claim and scope.

The read-only result distinguishes interrupted/unrecorded cleanup, no sample,
insufficient rounds, changed/incomplete scope, positive surviving resources,
and repeated scoped nondetection. Positive resource observations from any
post-cleanup round prevent a nondetection result; UNKNOWN also prevents it.

## Non-negotiable limits

- `REPEATED_SCOPED_NONDETECTION` is **not** physical absence, quiescence,
  teardown completion, Gate B, or approval to execute effects.
- The returned assessment always has `observer_custody=UNVERIFIED`,
  `coverage=INCOMPLETE_NATIVE_RESOURCE_SCOPE`, `effect_permission=NONE`, and
  `physical_absence_proven=quiescence_proven=teardown_proven=False`.
- Two reads from one local `/proc` scope are not independent physical
  corroboration. The current scope does not enumerate detached mounts, all
  foreign FD/namespace holders, surviving descendants, network obligations,
  or every native cgroup obligation.
- Native WSL/Bubblewrap, real crash/revocation/cancellation, independent
  custody, bounded physical disappearance and release gates remain blocked.

## Rejection evidence

Adversarial regressions cover positive surviving resources in early/late
rounds, UNKNOWN partial observations, insufficient rounds, cleanup interruption,
foreign scope, revoked owner lease and rehashed journal forgery. X0 requires
one original read owner, the original verified-journal read and required
negative evidence checks. QENV1 enrolls those regressions in the existing
PHYSICAL_OBSERVER ring. No read-only assessment output is admitted as an
Enforcer fact, a LACE custodian signature, or Game Tape terminal proof.

## Next

P3B2/P3C must exercise native resource allocation through the existing
Enforcer/LACE path on a real WSL/Linux host and independently corroborate
physical holder and teardown absence under the admitted Run/Step identity.
