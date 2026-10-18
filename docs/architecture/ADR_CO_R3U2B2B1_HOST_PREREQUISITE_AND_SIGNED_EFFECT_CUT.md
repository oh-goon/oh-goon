# ADR — CO-R3U2B2B1: host preconditions cannot be custody

**Status:** Accepted as hosted development change. Physical LACE authorization still deliberately non-issuing.

## Problem

The original cgroup-v2 validator used `os.access(W_OK)` to assess delegated-root write feasibility. This can be misleading on a mounted read-only cgroup2 filesystem, particularly for root. Meanwhile the original strict Bubblewrap path could construct a cgroup and network lease after journal reservation but *before* revalidating its original Enforcer effect authority. Finally an immutable `OriginalNativeCustodyRequirement` could be syntactically constructed with an absent executor generation, even though the production gate already refused all effects.

## Decision — no new authority plane

- The **existing** LACE delegated-root validator reads `statvfs(root).f_flag` and rejects `ST_RDONLY`, with errors failing closed before any effect. A positive result is only a prerequisite, not physical custody proof.
- The **existing** non-issuing `require_independent_native_parent_custody` rejects incomplete explicit custody scope before the unchanged hard refusal. A full dataclass is never a kernel-backed grant.
- The **existing** signed local-resource-intent closure is rechecked immediately before invoking each original native lease factory. These additional prework cuts do not substitute for authentication at the actual syscall when custody issuance becomes possible.
- The single original cgroup, restricted-egress, Enforcer and durable recovery owners remain authoritative. No diagnostics-owned Hashmarks work is implemented here.

## Limits

This development change does not create or authorize native resources. Independent custody must be provisioned by a real host with exclusive mutation control under adversarial tests. A writable cgroup2 root, inode recheck, process identity, FD binding, local lock or signed journal claim **alone** cannot protect the pathname at the destructive `rmdir` syscall. No Gate B/custody PASS may be inferred from these tests.

## Negative controls

Read-only/erroring `statvfs`, forged complete vs incomplete requirement, mocked zero kernel syscalls, and X0 mutations removing the original checks all fail closed. A real read-only cgroup2 host reports BLOCKED. Existing genuine delegated-kernel acceptance remains a separate exact-source host qualification.
