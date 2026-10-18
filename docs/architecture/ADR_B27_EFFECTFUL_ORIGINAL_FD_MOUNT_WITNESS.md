# ADR — B27 effectful witness consumes original Bubblewrap FD mount builder

**Checkpoint:** P1B27B2B3B2B3A · **State:** accepted DEVELOPMENT TEST CONTRACT, no physical security-gate promotion.

## Why

P1B27B2B3B2B2 proves live original lease FD inheritance to a Linux child but not
that Bubblewrap actually consumes the pinned O_PATH FD or sealed memfd. Synthetic
`/proc` fixtures cannot close this gap. Installing a second LACE scanner,
signer, executor, or purported physical attestor would create split authority.

## Decision

Add an **opt-in native mechanical witness to the existing QENV1
PHYSICAL_OBSERVER test ring**, using the same `BubblewrapProcessIsolation._command`,
`OriginalBindSourceLease.pin_prepared`, `_require_original_native_fd_handoff`
and supervised `run_command` already exercised by the production path. The
mechanical fixture supplies its own ephemeral, sealed memfd. It does not mint
trusted admission, a resource reservation, observer signatures, or a Run journal.

The test executes real Bubblewrap and checks that a writable pinned native bind,
a sealed `--ro-bind-data` payload and their kernel mount IDs appear *inside*
the actual sandbox mount namespace. It checks the ordinary supervised process
quiescence receipt. It deliberately does **not** claim kernel teardown absence,
mount-namespace holder completeness, independent observer custody or Gate B.
The fixture's development-only read-only `/` runtime root is never authorized
as a production workload root. Admission/effects for agents remain denied.

When `QUALIFY_B27_EFFECTFUL=1` is supplied, a missing bwrap executable or missing
FD options FAILS (never skips); an unprivileged-namespace/host denial is a
blocked native witness, not a hosted PASS. Default development qualification
remains unchanged, and cannot silently claim this optional result. All execution
still occurs in the original supervised QENV1 ring; no new runtime or certifier.

## Related bounded fix

Optional local LACE intent previously left `proof` undefined when its absent
path reached the mounted backend. Initialise it once **before** the conditional
reservation and never overwrite a returned original signed proof. Strict mode
continues to reject missing `RESOURCE_INTENT` prior to effects.

## Acceptance and remaining work

- Regressions must prove same original builder, same native FD lease, same
  supervised process call, no synthetic PASS, opt-in test-ring ownership,
  and the optional-proof/strict-proof branches.
- Exact-source normal X0/QENV1 and hosted tests must pass with no code-path
  duplication; exact-source transport remains mandatory.
- Native real Bubblewrap effectful witness must pass on exact candidate with
  matching PRE/POST source manifests and native kernel evidence.
- Separate physical Gate B still requires authenticated original-run kernel
  mount/namespace/FD holder identities, independently held observer authority,
  adversarial crash/cancel/survivor scenarios, and proven teardown absence.
- T0B2 custody, Gate A/C, AG-03/04, APP PASS and release remain blocked.
