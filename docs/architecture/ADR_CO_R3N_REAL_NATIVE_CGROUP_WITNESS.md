# ADR — CO-R3N real cgroup-v2 witness in original QENV1 qualification

**Status:** Adopted for development testing; native/independent certification not yet observed.
**Parent:** CO-R3M source SHA-256 `e9c8e56f89769f99748212ab54c8bba060fa6d760ba5c3a164c328f1ee5cdf8b`.

## Problem and decision

The CO-R3M pending-first cgroup allocation protocol was covered by real host
filesystem and child-process faults, not by real Linux cgroup-v2 allocation.
A passing hosted test cannot establish that kernel `cgroup.procs`, `cgroup.kill`
or `cgroup.events` have their intended semantics in the target delegated host.

Enroll one **optional native witness** in the pre-existing QENV1 `CORE` ring.
It uses the original `CgroupV2AttemptLease.open()`, `command_prefix()`,
`close()`, and `_reconcile_stale_cgroups()`; it creates no alternative native
allocation or recovery implementation. The test is activated **only** by a
caller-provisioned `QUALIFY_R3N_CGROUP_ROOT` on a cgroup-v2 filesystem.

The witness must preflight the caller-selected cgroup before effects: an
absolute canonical nonsymlink `oh-goon-native-*` directory subordinate to
an actual cgroup2 mount, owned by the invoking user, initially without
child cgroups or member processes, with original kernel controls exposed.
It must not run against the cgroup2 mountpoint itself, a tmpfs simulation,
shared production cgroups, or a read-only/unavailable delegated parent.

Its first native case creates an identity-bound attempt, attaches and executes
one child through the original command prefix, confirms `/proc/self/cgroup`
and observes the original owner retire its native cgroup and state.
Its second case lets a real subprocess die at the original `mkdir` cut and
requires the pending record and kernel directory to remain quarantined when
original reconciliation is attempted. **The unknown-identity cgroup is
intentionally not removed by this witness.** A fresh disposable delegated
parent is required for a subsequent run; manual physical recovery requires
independent custody and is outside this checkpoint.

No root -> `R3N_NATIVE_CGROUP_STATUS.txt: NOT_PROVEN`; other CORE tests still
run. Explicit root -> real native tests execute, and a failure fails CORE;
there is no pytest skip or synthetic successful receipt. Native qualification
results apply only to the exact candidate source. Even native success cannot
establish independent Enforcer/LACE generation binding, exclusive parent
mutation custody, escaped-writer absence, hostile `rmdir` atomicity, P3C,
Gate A/B/C, APP PASS, or release.

## Commands and boundaries

Provision a dedicated *empty and disposable* cgroup-v2 subtree using your
host's supported delegation mechanism, with an owner matching the WSL test
user. Do not use `sudo` to run OH-Goon and do not point at `/sys/fs/cgroup`
itself. An ordinary filesystem directory is **not** an acceptable substitute.

```sh
export QUALIFY_R3N_CGROUP_ROOT=/path/on/cgroup2/oh-goon-native-one-use
make qualify-development-wsl \
  QUALIFY_EVIDENCE_DIR=../OH_GOON_CO_R3N_NATIVE_EVIDENCE
```

The regular development qualifier remains available without this setting;
its status file explicitly records `NOT_PROVEN`. This checkpoint has not
performed a privileged native cgroup-v2 witness in the hosted environment.
