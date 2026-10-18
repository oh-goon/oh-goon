# ADR — P3B2A: require the one original physical observer roster in convergence windows

Status: accepted for development, **not** independently qualified or release-authorizing.

## Problem

P3B1 can legitimately journal partial, non-authorizing kernel observations (e.g., only
`cgroup: NOT_OBSERVED`). Its read-only convergence assessor could nevertheless label
**two such partial observations** `REPEATED_SCOPED_NONDETECTION`. The label did not
prove teardown, but disguised missing process, mount, network and FD-holder evidence.

## Decision and single owner

The existing `core.ports.physical_observation.ORIGINAL_KERNEL_COMPONENTS` tuple is
the **only** shape authority for the original shared Linux observer's component
projection. The *existing* `integrations.linux_physical_observer.observe_kernel`
double-scan must emit that exact roster, including order. The *existing*
`runtime.enforcer_lace_original_physical_sampling.assess_original_physical_convergence_unqualified`
checks that **each** post-cleanup journal sample covers the complete roster before
it can return `REPEATED_SCOPED_NONDETECTION`.

Any missing, extra, reordered or mixed partial component window is
`INCOMPLETE_OR_CHANGED_SCOPE`. Any `PRESENT` component still means
`VISIBLE_RESOURCES_REMAIN`; any `UNKNOWN` means incomplete. Earlier journal
samples remain readable: this is an assessment-only strengthening, not a
rewrite or migration of recorded physical facts. No extra observer, sampler,
signer, classifier, scheduler, journal or cleanup path is created.

`REPEATED_SCOPED_NONDETECTION` **never** establishes physical absence,
quiescence, teardown, independent custody or the Gate B predicate. All such
bits remain false. In particular the bounded local /proc scan cannot see all
hidden PID namespaces, detached mounts, kernel-held FDs or undeclared host
network/cgroup obligations; the component roster is a *minimum shape check*,
not a complete physical resource inventory.

## Validation

Regression tests run through the original Fleet recovery claim, two-cut journal
sampling and verified events. They inject missing, reordered and mixed
component rounds; present cgroup/subtree/process/mount/network/holder claims;
and an exact roster of repeated nondetection. X0 mutation guards reject removal
of the sole roster, reader enforcement or assessment fence. QENV1 selects the
regressions in its original `PHYSICAL_OBSERVER` ring.

## Independent acceptance still required

A true P3 Gate B needs real effectful original Run/Step resource allocation,
source-FD/kernel-mount provenance, bounded cancellation/crash/revocation and
surviving-holder scenarios, complete cgroup/network/namespace resource
inventory, original authenticated physical-fact publication, independent
observer custody and corroborated absence. P3B2A alone does none of these.
