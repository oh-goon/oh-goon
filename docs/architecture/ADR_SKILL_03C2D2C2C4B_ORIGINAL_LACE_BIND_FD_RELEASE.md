# SKILL-3C2D2C2C4B — Original LACE local bind-FD release accounting

## Problem

The original Bubblewrap native process owner pins native bind-source inodes
through one `OriginalBindSourceLease`. Its existing context-manager `close()`
closes the descriptors on scope exit, but suppresses close errors. A normal
`CommandResult` therefore did not depend on confirmation that the original
process's bind-source FD roster was released. It is unsafe to interpret a
best-effort local FD close as physical source unmount or teardown.

## Decision

Add **one** post-process release cut on the **existing** `OriginalBindSourceLease`
without changing the signer, grant, original `RESOURCE_INTENT`, Agent staging,
physical sandbox backends, or LACE cleanup authority. The synchronous
Bubblewrap `execute()` calls `release_pins_after_process()` after the original
`run_command()` supervisor returns and *before* publishing a `CommandResult`.

That cut revalidates the pinned roster, attempts to close every descriptor,
checks that each number is not still open, and fails rather than returning a
successful result if any FD is changed, still live, or cannot be closed. No
second close attempt is presented as a verified success. A failed process
invocation continues using the already existing context-manager best-effort
cleanup; it produces **no** local FD-release confirmation. Neither branch
unseals or deletes staged source files or directories.

## Authority and limits

- `OriginalBindSourceLease`: the single native FD owner; no new ledger or
  source-permission/teardown issuer.
- Original Bubblewrap backend: one synchronous post-process callsite, preserving
  the existing pre-effect run/step/enforcer and cgroup/network fences.
- Local FD release is **not** a physical quiescence, mount absence, or teardown
  receipt. Cross-process and foreign mount namespace FD holders are unobserved.
  Closing a number can itself race concurrent same-process FD-table mutation;
  such reuse is rejected if observed, and exclusive host FD custody is still a
  native qualification requirement.
- Agent `RESOURCE_INTENT` and native launch remain refused. An Agent Step may
  not treat this local release as a grant or source-unsealing permission.

## Follow-up

Original LACE must establish physical process/cgroup and mount namespace
quiescence, then independently qualify native immutable-directory unsealing,
controlled deletion, revocation, restart, and recovery on an actual Linux/WSL
host before positive Agent execution or terminal teardown proof.
