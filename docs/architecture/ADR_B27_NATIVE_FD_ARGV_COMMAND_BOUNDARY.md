# ADR — B27 native FD mount options never consume Step arguments

**Status:** Implemented development-only in `P1B27B2B3B2B3B`.

## Why

The original pinned native-source lease compared FD bind triples across the
*whole* Bubblewrap argv. After `_command` appends `--` and the Step command,
Step-controlled strings such as `--bind-fd 123 /target` may be interpreted
as evidence for a real mount. This can accidentally reject a valid command,
**or mask an omitted native mount** when the command carries the missing
triple. This violates fail-before-work admission and lets an untrusted child
argument influence parent mount verification.

## Decision

Keep the one original `OriginalBindSourceLease` owner, `_prepare_mounts`,
`_command`, `_require_original_native_fd_handoff` and `run_command`.

1. `_original_bwrap_options` projects argv before its first `--` delimiter.
   It may operate on unfinished builder args (no separator yet), or complete
   launch args (with separator), without interpreting Step arguments.
2. Both original pathname-to-FD conversion and original FD-argv verification
   consume that exact projection, rather than duplicating a parser/owner.
3. At the **final handoff** (where the entire command must be complete), a
   missing/empty command separator is refused before any `run_command` call.
4. The original opt-in real Bubblewrap witness carries a child argument
   deliberately resembling a native mount option; the mounted resources and
   mounted contents still have to match the original admitted roster.
5. Original X0 and QENV1 PHYSICAL_OBSERVER selection enforce these contracts.

No new signer, admission cut, runtime, scheduler, source resolver, resource
publisher, physical-fact classifier or privileged alternative code path exists.

## Negative evidence and remaining gates

Hostile tests require that a missing bind cannot be substituted by child argv,
command argv containing mount-like flags cannot reject an otherwise valid
launch, and the final command delimiter cannot be omitted. The original source
manifest and ordinary qualification ring selection are immutable evidence.

This does **not** prove kernel consumption, FD/content immutability after
admission, exhaustive holder census, physical absence, independently signed
LACE facts, Gate B, Agent effect permission, or release. The exact candidate
still needs native WSL qualification with `QUALIFY_B27_EFFECTFUL=1`.
