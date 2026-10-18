# ADR — B27 original LACE visible-holder race fences

Status: DEVELOPMENT ONLY (P1B27B2B3B2B1), 2026-10-14.

## Decision

The one existing read-only Linux LACE observer is the sole owner of visible
namespace-FD and mount-FD sampling. Reject as `UNKNOWN` when a visible
process roster changes across the census, a namespace FD name or resolved
namespace handle changes during an individual process scan, or the FD number
has a different `fdinfo.mnt_id` when rechecked. This closes local intra-scan
TOCTOU cases missed by the original two full snapshots. Do not introduce a
new kernel scanner, agent runtime, fact signer or independent diagnosis path.

Preserve scopes and semantics: `NOT_OBSERVED` means visible scoped nondetection
ONLY. Unknown or contradictory kernel observations cannot authorize work,
prove teardown or establish physical absence. Holder visibility is not proof
of mount source inode provenance or actual Bubblewrap `--bind-fd` consumption.

## Verification

- Focused racing `proc` fixtures: FD reuse, same-number mount substitution,
  namespace-handle swap, PID and FD roster changes and disappearance.
- Permanent X0 AST checks require executable guards in the *original* probes.
- Existing QENV1 `PHYSICAL_OBSERVER` selection requires the focused module.
- Real host proof remains separate: actual original effectful FD-bind mount,
  signed original intent-to-kernel identity correlation, independent custody,
  crash/teardown convergence and absence with all holders accounted for.

## Alternative rejected

An extra LACE physical observer or an event signer per FD is a parallel
security boundary and does not solve intra-scan race correctness.
