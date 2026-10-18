# ADR — SKILL-3C2D2C2A: One original Agent mount-tree isolation rule

## Decision

The existing original `OriginalMountIntentDescriptor` is still the only native
mount commitment owner. Its `/opt/oh-goon/agent-source` read-only bind must not
have another mount target that is an ancestor or descendant of the Agent source;
otherwise a later mount can shadow the source, including a writable child mount.
The root `/` mount is the standard native baseline and is exempt; the exact
Agent read-only bind is verified separately by the existing strict descriptor.

Use one shared pure check, `require_unshadowed_agent_mount_targets`, on three
existing original effect cuts:

1. `OriginalMountIntentDescriptor` validates the signed native and managed tree.
2. `OriginalBindSourceLease.pin_prepared` checks the complete FD-source bind roster
   before opening the first bind source FD.
3. `_require_original_native_mount_argv` checks *all* command-time mount targets,
   including sealed `--ro-bind-data` runtime files that are outside static roster.

The old absent-Agent wire encoding and the normal command Step path remain intact.
No new signer, ledger, LACE provider, second authorizer, or Agent execution path.

## Prohibited before native effects

- Native `--bind` or `--ro-bind` at Agent descendants or ancestors other than `/`.
- Managed input bind into an Agent source descendant or over its parent.
- Sealed runtime-file data bind into the Agent source or any enclosing parent.
- A descendant mount under the Agent source when no Agent grant/source exists.

A checked mount tree is **not** immutable descendant content, a physical kernel
mount inventory, revocation, process containment or teardown proof. Existing Agent
RESOURCE_INTENT and native launch refusals must remain active until those physical
gates are independently qualified. The general original-run native guarantees
must not be weakened.

## Regression and qualification

Run the dedicated mount-tree tests, the prior Agent source/grant and FD-native
roster tests, and canonical architecture tests. Re-run the focused set from a
freshly extracted candidate; record known inherited mutation-test failures
separately instead of disguising them as passes. No native WSL certification is
claimed.
