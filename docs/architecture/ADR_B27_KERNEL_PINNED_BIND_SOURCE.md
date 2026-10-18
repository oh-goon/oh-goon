# ADR — Kernel-pinned Bubblewrap bind sources (P1B27B2B2A)

**Status:** development implementation; physical Gate B **BLOCKED**.

## Decision

Extend **the existing** Fleet/Enforcer → original `RESOURCE_INTENT` → Bubblewrap →
LACE path; do not add an Agent launcher, verifier, signer, observer, or effect
classifier. Linux Bubblewrap is required to support `--bind-fd` and
`--ro-bind-fd`. Versions lacking these options fail before physical allocation.
Do **not** fall back to `--bind` with a resolved pathname or
`/proc/self/fd/<N>`; ordinary pathname binds permit a source swap and
Bubblewrap may close FDs not declared by FD-aware options.

After the original signed reservation succeeds, the existing Step `execute()`
pins every predictable source from `prepared_original_native_mounts(request)`,
the admitted managed inputs and admitted host-tool snapshot sources. Walk the
source's path components with Linux `O_PATH|O_NOFOLLOW`, reject symlinks, hold
every opened FD and its `(st_dev, st_ino, st_mode)` for the full Bubblewrap
handoff. The existing native argv checker runs **before** the single FD
conversion; then the lease checks FD-native argv, device/inode/mode and passes
exactly those FDs to `run_command`. The original atomic memfd lease remains
separate because it already uses `--ro-bind-data` to copy sealed bytes.
The existing resource owner supplies the dynamic delegated cgroup path, whose
FD is pinned when available. No second resource projection is allowed.

## Security meaning and limits

A rename/replacement of the original source **pathname** after pinning does
not alter which FD object Bubblewrap will bind. A swap of an inherited FD is
rejected before launch. The original signed intent still binds the admitted
path/identity, **not a kernel inode unavailable at signing time**. In-place
mutation of writable source objects and changes to the contents of a mounted
directory remain outside this proof. Native Bubblewrap process exit, mount
namespace custody, FD-holder recovery, detached mount absence, and teardown
are **not** proven merely by verifying the argv. Those are LACE-owned
observations on the existing shared physical-fact path, requiring native WSL
qualification with the selected Bubblewrap binary before Gate B can advance.

## Regressions and restrictions

- Reject missing/replaced kernel source FD, forged FD argv, missing/extra binds,
  unsupported bwrap, symlink ancestors/leaf, partial roster, and dead-branch
  or removed X0 call edges.
- Keep original signed reservation earlier than FD pinning and resource
  allocation; runtime `execute()` MUST supply its FD lease.
- Preserve QENV1 prepare-once/verify-once with no dependency work in rings.
- Agent Steps, Gate A/B/C, AG-03/04 and release remain blocked.
