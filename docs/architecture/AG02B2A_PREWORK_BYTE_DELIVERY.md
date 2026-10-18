# AG-02B2A — byte-exact agent prework delivery contract

**Status:** implemented as a non-executable, non-admitted development slice on exact AG-02B1 source. **NOT** the AG-02B2 signed Run/Step source transport or release qualification.

## Architectural decision preserved

We will not build an agent runtime inside OH-Goon. We extend the existing Plan/Goon/Step and original Fleet/Enforcer/LACE authorities to safely host agent workloads.

`FleetPlanResolver.prepare_agent_step_handoff()` is the only source-graph and typed Step binding. Its `FrozenAgentStepHandoff` already owns byte-exact AG-02A captured source. AG-02B2A adds deterministic `encode_prework_delivery()` and `decode_prework_delivery(..., expected_handoff_identity=...)` **on that same value object**. No new source reader, Plan interpreter, database, CLI, runner, scheduler, network client, effect permission or observer is introduced.

The packet contains: source/Plan graph and Atomic-owner identities, authored and resolved source provenance, original Step ID/harness/task/timeout, exact repository and complete Agent Skills file bytes, per-member hashes, closure identities, and the full original handoff identity. It contains **no host filesystem paths, live file handles, bearer secret, provider credential or admission token**. This is sensitive source data: do not log the raw delivery, forward it to an LLM/provider, or expose it through a public API.

The receiver validates a strict canonical JSON schema with a hard encoded-length limit, bounded file count/sizes, canonical base64, duplicate-key and duplicate-path rejection, path safety, per-file SHA-256, full-tree closure identity, original source capture identity, exact Step handoff identity and *caller-supplied expected handoff identity*. Receiver never reopens the source location or invents defaults. The caller-supplied expected identity is currently a **prework comparison** and NOT original Enforcer admission, cryptographic provenance or a bearer capability. No facility for replay prevention is asserted.

`authority=PREWORK_NOT_ADMITTED`, `effect_permission=NONE`, and `transported_immutable_snapshot=False` stay frozen. The canonical Resolver `resolve()` still rejects `uses: agent` before any effects. The delivery remains a codec, not a signed or durable transport.

## AG-02B2B remaining required production work

1. Use only the already captured byte packet, stored under the original Fleet/Run-source custody owner (not an agent-specific database or transfer service). Associate exact source, original Step import occurrence, Run ID, lease/control epoch, enrolled issuer/trust epoch, executor generation, and selected backend. External issuer/OS root custody cannot be simulated.
2. Within the **original owner-bound SQLite/Journaling transaction**, authenticate original `RUN_ADMITTED` and `RUN_SIGNER_EPOCH_BOUND`, commit the source byte identity and a one-shot delivery receipt. Do not append a separate authoritative agent journal or create a second `RUN_ADMITTED` owner. Re-check the original admission/lease/trust/generation after any bounded preparation and before effects.
3. Prove at most one claim/delivery for the Run/Step/attempt/generation; a restart must detect the exact committed custody state. Duplicate/replayed and same Step/different bytes fail closed. Never treat a process-local set as durable proof.
4. AG-02B3 must extend the **same** resolved Step/sequencer call chain. Until AG-03/AG-04 and B27 plus independent original Gate A/B qualification, no effectful agent launcher, privileged tool, model call, network egress or source materialization outside LACE is available.
5. Native exact-source WSL, real provisioned issuer, independent roots, kernel/resource observations, browser/Q0, Gate C, APP PASS and release are separate gates. Hosted codec tests never promote them.

## Regression evidence

Same source-owner test module proves deterministic round-trip after source removal, no file re-read, exact binding, source substitution rejection, invalid identities, forbidden effect claims, duplicate JSON keys/paths, noncanonical encodings, unsafe member paths and bounded payloads. Also rerun the original source capture and agent type/architecture tests. The original WSL `AGENT_PREWORK` ring already selects this test module; its next native execution is pending.
