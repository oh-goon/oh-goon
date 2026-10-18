# AG-02B2B1 — original signed Fleet prework source commitment

Status: **DEVELOPMENT ONLY, NOT AGENT EXECUTION OR COMPLETED AG-02B2**.

## Invariant

**Resolve once, transport once, execute once, report once.** The original
AG-02B2A `FrozenAgentStepHandoff.decode_prework_delivery` remains the only
byte/source validator. A read-only service bridge invokes one new method on
`CausalRunJournal` *inside the original caller-owned SQLite write cut*. No
parallel journal, coordinator, resolver, admission or runtime is added.

## What this implementation proves within a test fixture

- Already-open Fleet write transaction; original active Run, exact lease,
  cancellation-free, and not recovery-only.
- Verified original signed `RUN_ADMITTED`, `RUN_SIGNER_EPOCH_BOUND`, matching
  current signer-trust grant, and exact admitted Step/generation/teardown scope.
- Original accepted Step envelope explicitly contains both `workload_kind=agent`
  and the expected immutable handoff identity. Current production ingress does
  not yet create that typed envelope, so this cannot admit effectful agent work.
- Existing AG-02B2A canonical receiver validates the full frozen source packet
  once, including its original Plan/Goon identity; no filesystem reopen.
- One MAC-protected, chain-verified `AGENT_SOURCE_PREWORK_RESERVED` per original
  Step, with a domain-separated packet digest and no plaintext packet or
  repository/skills bytes in the journal. The existing committed transaction
  is the only atomicity owner. Signature and duplicate-event verification
  happens in original journal verification, not another projector.
- Crash before commit rolls back. Cancellation, stale epochs, changed bytes,
  missing signed admission, foreign signer, recovery-only lease and duplicate
  attempts reject without publishing a new event.

## Not claimed

The original production Fleet issuer and six externally provisioned OS-owned
roots are **not qualified**. The actual original Run/Step claim owner has **not
been wired to invoke** this non-executable prework bridge; only the development
journal fixtures exercise it. The journal stores a commitment, **not encrypted
source bytes or a durable sandbox handoff**. There is no consuming Step, no
native/remote physical-resource proof, no provider/MCP egress authority, no
sandbox action, no data-disclosure right, and no APP PASS. A signed commitment
is not an execution credential. In particular, no Step may launch solely on
this event's presence or from a source digest.

## Next: AG-02B2B2 / AG-02B3

Bind the production original Step occurrence/attempt allocator and caller-owned
lease cut to this reservation, with verified immutable source custody in the
selected original sandbox backend. Never add an agent-specific launcher, fake
signer or alternate journal. Keep execution fail-before-work until AG-03,
AG-04, B27 and independent admission/physical gates qualify it.

Exact-native WSL `AGENT_PREWORK` ring and pinned lock validation are still
pending for this checkpoint. Hosted tests with deterministic keys are not
production attestation.
