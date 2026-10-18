# ADR — SKILL-3C2D2C2C1 — original signed mount-forwarding integrity

## Context

A real test regression was reproducible on the SKILL-3C2D2C2B source:
`test_mount_order_mutations_are_rejected[disable-identity-forwarding]` silently
tried to delete an obsolete keyword expression and never mutated the owner.
Moreover, the X0 check only counted keyword names, so a rewritten keyword
with a `None` value could appear structurally present.

## Decision

The existing `BubblewrapProcessIsolation.execute` remains the only producer
of `_command(signed_mounts_identity=...)`, which in turn passes the same value
to `_prepare_mounts`. The original X0 architecture checker verifies the exact
AST-bound expression at both edges. The mutation fixture applies its edits
to the CURRENT callsite and fails if the replacement did not happen.

At the native mount builder cut, any request with `local_resource_intent`
requires a non-None `signed_mounts_identity`, compared to the original
`local_resource_identities(request)['mounts']` before runtime preparation.
No reconstruction of journal authority or second grant owner is allowed.

## Scope / non-goals

This prevents one authorization omission and restores meaningful mutation
coverage. It **does not** make POSIX staged descendants immutable, does not
validate an operating-system mount, cannot issue a real Agent execution grant,
and does not qualify native WSL process containment, revocation or teardown.
The existing `require_original_agent_source_mount_authority` refusal remains.
Do not present green hosted tests as physical custody evidence.

## Acceptance

- Missing or forged mount identity refuses before `verify_runtime_root`.
- Removing forwarding or replacing either forwarding edge with `None` is
  caught by X0 with a real applied mutation.
- Original no-local-resource-intent paths remain backward compatible.
- Original Agent refusal, source-stage and roster tests still pass.
