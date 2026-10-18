# ADR — Original atomic/native FD handoff (P1B27B2B3B2B2)

**Status:** Implemented as development-only fail-before-native-launch protection. **Not physical Gate B evidence.**

## Decision

In the single existing `BubblewrapProcessIsolation.execute` path, the original
`AtomicRuntimeAuthorityLease` and `OriginalBindSourceLease` must jointly validate
the **one** descriptor inheritance roster passed to the existing `run_command`.
The original signer, `RESOURCE_INTENT`, mount builder, cgroup/network owner,
LACE observer and Enforcer classifier do not change.

Original signed `RESOURCE_INTENT` -> original native mount roster -> O_PATH
source pins + sealed atomic memfds -> original Bubblewrap validated FD argv ->
**single pre-spawn disjoint FD handoff** -> existing `run_command(pass_fds=...)`.

The original helpers, not a second independent policy implementation, check:

1. Atomic memfd contents/seals/receipt/inode from the admitted runtime lease.
2. Every native bind's unchanged O_PATH FD device/inode/mode, plus Linux O_PATH
   and FD_CLOEXEC flags; every FD-aware argv entry must match the original pin.
3. The merged descriptor roster has no missing atomic/native lease, duplicate,
   cross-lease FD-number alias, standard descriptor, negative value, or non-int.
4. Only that verified merged tuple is passed to the original command launcher.

**Failure ownership:** the original prelaunch owner raises `FleetPlanInvalid`
(or existing runtime-authority divergence). Original resource teardown still
runs via the existing `finally` and the LACE recovery owner remains authoritative.
There is no second observation/signing/launch mechanism.

## Negative coverage

Native O_PATH pin replaced with O_RDONLY on the same inode; CLOEXEC cleared;
atomic/native FD number collision; invalid FD; closed pin; substituted mount
FD argv; dead/moved original prelaunch check; wrong inherited tuple; QENV ring
omission. A real native shell child through `run_command` verifies the FD
inheritance route without introducing a production launch path or pretending
to exercise Bubblewrap mounts.

## Unproven obligations / next checkpoint

- Native effectful FD-bind Bubblewrap operation through the full admitted
  original Run/Step with pinned source and signed mount/attempt lineage.
- Physical kernel mount and namespace-holder evidence from the original LACE
  owner, independent custody, adversarial process death / cgroup/network
  cleanup, and externally verified bounded teardown absence.
- Original Agent source effect gate, AG-03/04 disclosure, Gate A/B/C, APP PASS
  and release. No hosted regression or local subprocess proves these gates.
