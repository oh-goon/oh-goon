# ADR — One canonical Step effect order (X0)

**Status:** Accepted execution-order contract; Agent Step effect admission remains **BLOCKED**.

## Decision

All workload kinds use **one existing OH-Goon execution path**. The original
Fleet Run journal and Enforcer own admission and the transition to physical
effect permission. A typed Agent Step is a workload of that path, not an agent
runtime, separate scheduler, journal, mount authority, or policy engine.

The permitted causal order is:

1. Resolver freezes source bytes and the exact Plan/Goon/Step identity **once**.
2. Original Enforcer admits the Run/Step, binds signer epoch, lease and executor generation.
3. For agent workloads, the original Step attempt reserves, stages and verifies
   immutable agent source, producing a **non-effect** journal commitment.
4. **Not implemented/blocked:** the original admitted attempt would bind a
   stage-specific *read-only* source mount and independently verifiable custody
   into its one signed RESOURCE_INTENT. No stage fact grants mount permission.
5. Original `CausalRunJournal.publish_local_resource_intent_in` checks admitted
   workload kind and signed predecessor evidence in the **same write cut**.
6. Only the original admitted LACE backend may perform physical effects
   corresponding exactly to the signed intent; the physical observer records
   actual allocations, namespaces, mounts and resource lifetime.
7. Original Step sequencer executes; Enforcer classifies based on authenticated
   observations; teardown must be independently observed before terminal seal.
8. Game Tape projects original immutable evidence and cannot redecide policy.

No later phase may manufacture evidence that a predecessor phase did not
establish. Unknown authority or resource completeness **fails before work**.

## Non-duplication contract

* The original `CausalRunJournal` remains the **only** RESOURCE_INTENT author.
* `fleet_plan_local_lace_intent.py` and Bubblewrap consume an existing typed
  mount descriptor; they may not create another admission decision.
* `AGENT_SOURCE_PREWORK_RESERVED` and `AGENT_SOURCE_PREWORK_STAGED` never imply
  an allowed native mount. A missing stage event is NOT a fallback grant.
* Test fixtures, diagnostic readers, recovery paths and Game Tape are not
  substitute authorities.
* The existing X0 architecture check enforces original writer uniqueness,
  authenticated predecessor inputs and unconditional pre-append placement.
  Source-mutation regressions must fail on guard removal, reordering,
  falsification, and duplicated publishers.

## Acceptance and evidence

Hosted tests demonstrate **only the code-order contract**. Real provisioned
signers, native Bubblewrap consumption, independent physical resource custody,
negative egress/data-leak testing, terminal Gate C, APP PASS and release remain
independent and BLOCKED. Once the signed mount step is implemented, extend the
**same** original predecessor function and causal tests; do not patch callers
or add a new authorization path.
