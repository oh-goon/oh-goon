# Deterministic shipped template catalog (M139)

## One catalog owner, one exact-byte source identity

The production `PlanTemplateStore` and `GoonTemplateStore` both use
`runtime.template_discovery.discover_template_packages` for discovery. They
read individual packages using `template_package`, which checks the same
canonical layout. There is **no** independently authored `catalog.yml`,
search index, fallback lookup, extension inference or alias registry.

The only address is `category/name`. Every package has exactly two authored
files: `plan.yml` or `goon.yml` and `README.md`. Path owns logical identity;
README owns human explanation; YAML owns executable meaning. Template class
comes from the canonical category policy, never README or YAML metadata.

The discovery owner sorts canonical keys and rejects noncanonical directory
names, unexpected package members, and symlink package paths. Known transport
sidecars are discarded by the common authored-source-membership owner, not by
ad-hoc catalog logic. A syntactically similar package name is never a fallback
for a missing or malformed requested key. Distinct canonical names remain
distinct even when their current source bytes happen to match: do not dedupe
packages by content hash.

`FleetPlanSourceSnapshot` captures each Plan/Goon source byte stream once,
decodes, hashes and compiles from *that same snapshot*. Catalog summaries and
read documents expose `sha256` of their **YAML source bytes** for both Plans
and Goons. The hash is not over a README, a package directory, the catalog,
compiled contract, or a later reread. Changing README prose may change the
human catalog description without changing the source digest. Changing YAML,
including line endings, changes the digest.

## Determinism versus staleness

Catalog listing and document reads are separate calls: their `sha256` values
make an intervening source change observable. They are **not** a transaction,
a lock, or a durable certification receipt. Admission must use its captured
canonical resolved authority and its staleness/identity checks, not trust a
previous catalog response as executable permission.

The API's OpenAPI and TypeScript projections are derived from backend source.
When a source-owned catalog field changes, regenerate them from the canonical
backend generator, then run `check-generated`. Do not hand-edit generated
frontend interfaces. Static authoring and the real stores own these rules; QA
regressions exercise creation-order changes, source-byte edits, case aliases,
symlink alias rejection and summary/read identity convergence.

See [template input resolution and transport](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md)
for how a selected Plan moves from catalog visibility into production admission.
Catalog ordering never creates execution authority.

## Complete source census

The existing `oh-goon-template-ux-a-inventory` diagnostic enumerates every Plan
and Goon through these same stores and every current `examples/` directory.
Run it from the exact initialized checkout and keep its report outside source:

```sh
uv run --offline --frozen --no-sync --project backend oh-goon-template-ux-a-inventory \
  --root . --output /tmp/oh-goon-template-inventory.json
```

The versioned report includes the observed Git commit, tracked-worktree change
state, source-byte digests, category classification, compiled direct imports,
requirements, declared capabilities and outputs, README presence and example
classification. Counts come from the detected membership, not a frozen count
allowlist. New/unclassified examples make census coverage incomplete. Example
README classifications describe their proof scope; they do not decide
executable behavior or native readiness. The census reads each package source
once and projects its canonical compiled snapshot.

A commit label alone does not describe uncommitted edits. File digests identify
the observed source cut; this diagnostic is not an atomic admission snapshot,
Run receipt, external-service probe or usability trial. Historical UX evaluations
retain their original counts and conditions.
