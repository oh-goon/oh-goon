# Structural authority guard

Oh-Goon treats simplicity, ownership, and race safety as executable architecture properties.
The guard exists to prevent a fixed bug class from reappearing through another path; it is not runtime policy and is never consulted by APP execution.

The guard must protect semantic ownership, not implementation shape. It must not convert physical source size, line positions, formatting, lint/debt metrics, documentation shape, or other maintainability proxies into APP certification authority. When a static proxy is the only thing being protected, replace it with the semantic invariant or keep the observation diagnostic. See `FAIL_BEFORE_WORK.md`.

## What the guard protects

The repository must keep one canonical owner for each critical responsibility:

- `make test` is a one-line delegation to the logged development-test frontdoor; it may schedule only canonical fast feedback targets and must not become APP certification.
- `make certify` is a one-line delegation to the logged certification frontdoor, which owns exactly one shared workflow runner; APP Goon order belongs only to the APP Plan.
- APP verification carries no strict release/CI executor authority: the certification frontdoor classifies APP verification and strips the canonical strict-executor environment before static admission or any APP bootstrap/orchestration work, the workflow runner strips strict transport namespaces without importing product-domain authority, delegated systemd re-entry rejects non-strict workflow purposes, child processes cannot inherit certification executor/cgroup/systemd control-plane authority, and the APP local-runtime Goon delegates only to `make local-runtime-verification` rather than the production-shaped strict runtime acceptance.
- APP, PACKAGE, and certification evidence share the same workflow bootstrap owner rather than rebuilding repository identity, schema, state, Plan locks, or Plan execution in Make.
- `backend/src/oh_goon/workflow.py` may call backend capabilities but may not import/reimplement backend domain authority such as repository identity, schema, Fleet history, or APP-success evidence.
- Pre-Plan startup checks only prerequisites needed to launch Oh-Goon. Node/npm/frontend/dev/browser semantics remain owned by APP Goons.
- Durable APP/PACKAGE success comes from canonical Run evidence. Mirror pass-marker files are forbidden.
- Frontend HTTP/write transport remains centralized through `ApiClient` / mutation transport instead of feature-local transport paths.
- Destructive API operations require an explicit target; Fleet Run cancellation remains identity-bound from the displayed Run through the command boundary.

The static contract is `scripts/architecture/structural_authority_contract.py` and is part of `make architecture`.
The APP tooling-test Goon also executes its regression tests, so the current repository shape is checked during APP certification without adding another APP orchestration step.

## Race safety is behavioral, not inferred from static structure

Static architecture checks cannot prove async ordering. Race-prone ownership therefore has deterministic behavioral tests alongside the static guard:

- an older read completion cannot overwrite a newer identity;
- an A → B → A identity cycle cannot revive the first A completion;
- stale/superseded event polling cannot restart an old watcher;
- destructive confirmation is cleared when displayed Run identity changes and cannot be inherited through an A → B → A transition;
- server mutation results are verified against the explicit command identity/authority before presentation adopts them.

These tests use deferred promises/fake timers/identity tokens rather than sleeps.

## Closure rule for future changes

When a new parallel-path, mirror-state, owner ambiguity, or race bug is found:

1. Fix the concrete defect at the canonical owner.
2. Identify the general invariant that would have made the defect impossible.
3. Add a negative regression fixture or deterministic race test for that invariant.
4. Reuse the existing architecture/test boundary; do not add a new policy registry, startup controller, Plan, Goon, or success marker unless the product itself genuinely needs a new owner.
5. The bug class is not considered closed until both the concrete fix and the invariant test pass.

This keeps the guard derived from real owners. There is intentionally no `authority.yml`, mirror ownership registry, or runtime guard database.
