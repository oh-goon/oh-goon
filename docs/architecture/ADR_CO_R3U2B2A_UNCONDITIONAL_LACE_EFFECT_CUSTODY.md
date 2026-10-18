# ADR — CO-R3U2B2A: fail-closed original native effect custody

**Status:** Accepted for hosted development; physical authorization not issued.

## Context

CO-R3U2B1 sealed process/controller cuts only when a path classifier detected
cgroup2. `_is_cgroup2_path(path)` is a helpful *read-only* kernel
classification, not an authority boundary. Mountinfo may be unreadable,
incomplete, stale, hidden by another mount namespace, or return false for an
unobserved effect path. Using `False` as a grant was fail-open.

## Decision

Keep the one original OH-Goon LACE custody gate and require it **without any
condition** at all original native cgroup effect helpers. Never interpret
mountinfo errors, negative classifications, path absence or cached inode
identity as permission to mutate. The original gate currently refuses every
request because no independently provisioned kernel-exclusive owner exists.

Read-only cgroup mount classification remains an informative check for
validation/recovery *observation* and not permission. Tests simulating an
externally provisioned kernel owner must explicitly replace the original
gate inside a single test function; such monkeypatches do not alter deployed
source and cannot count as physical host proof.

Keep original Enforcer meaning/admission, original LACE mutation/teardown,
and authoritative Game Tape projection unchanged. No Hashmarks dependency,
owner, method migration, evidence-intelligence or native sensor is introduced.

## Acceptance / negative controls

- Inject `False`, missing mountinfo, and inspector errors: the gate refuses
  before any cgroup write, removal, allocation, prefix, pipe or process move.
- Prevent premature records/locks or false cleanup success.
- X0 architecture rejects moving native custody behind a predicate.
- Existing post-custody syscall/inode tests remain explicitly qualified as
  simulation, never real-native evidence.

## Deferred blockers

CO-R3U2B2B must deliver genuinely OS-enforced independent parent/namespace
mutation exclusivity, current authenticated Run/Step epoch + executor
generation binding, destructive syscall-time identity protection or refusal,
and real delegated-host negative controls. Only independently qualified native
proof may convert the non-issuing gate. `rmdir(name, dir_fd=...)` by itself is
not identity-conditional; repeated stat and local locks remain insufficient.

Native WSL, Gate A/B/C, APP PASS and release remain **BLOCKED / NOT PROVEN**.
