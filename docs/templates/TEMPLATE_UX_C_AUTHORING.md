# UX-C — Modular Plan authoring (development, read-only)

UX-C proposes ordinary Plan text and a strict caller values-file draft. It **never**
creates a Run, changes a Schedule, saves a Plan, resolves sources twice for execution,
or controls a Fleet/LACE admission path. The only source authority is the existing
Plan compiler, imported Goon source graph and input binder.

## Discover and compose

Run `oh-goon-template-ux-c-draft --catalog` for the **existing** catalog classification
and Goon descriptions; it is not a separate schema registry. Read an original shipped
Plan's SHA-256 from the Plan template inspection response. The digest is an
**optimistic source-revision check**, not an imported-closure digest or permission.

A request JSON for `python/standard` may look like:

```json
{
  "edits": [{"op":"move", "from":1, "to":0}],
  "values": {
    "release_channel":"production",
    "revision":"abcdef1234567890abcdef1234567890abcdef12",
    "coverage_threshold":90
  }
}
```

Use:

```sh
# The exact root digest must come from inspecting this checkout's template.
uv run --project backend --frozen oh-goon-template-ux-c-draft \
  --plan python/standard --expect-sha256 "$ROOT_SHA256" \
  --request team-request.json --output draft-report.json
```

The response contains `plan_yaml`, `values_yaml` and `report`. They are proposed
**text**, not saved files or admitted work. `--output` writes only diagnostic JSON
where explicitly selected. The report intentionally redacts caller values; copied
`values_yaml` contains the supplied values and should be handled as private local
configuration. Do not put credentials/secrets into caller values files.

Operations address **zero-based occurrence positions**: `add` (`at`, `import`),
`remove` (`at`), `move` (`from`, `to`), `replace` (`at`, `import`). A mapping import
may contain `import`, `as`, `with`, or `failure_impact`; it cannot contain a Step,
command or a new interpreter. The compiler validates alias collisions and all
producer/input references. For an input projection edit, explicitly pass
`"projected_inputs":["..."]`: definitions, typing and defaults remain owned by
Atomic Goons. Editing references does not reorder a Plan to rescue dependencies.

Use independent team requests to produce `values/dev.yml` and
`values/staging.yml`. This is **not** an overlay/merge stack; there is no implicit
precedence, inheritance, nested file reference, environment fallback, or secret
injection. Resulting caller YAML has exactly one `inputs` mapping and uses the
existing reader/binder. A saved Schedule keeps its immutable original values;
changing this draft does not change that Schedule. Use the original authorized
reschedule entrypoint when a new Schedule is actually desired.

## Inspect the diff

`report.before` and `report.after` list original compiled import IDs and their
ordered identities; `resolved_comparison` uses the existing Fleet resolver and
reports Step IDs, Step count, order, and hashes of capability contracts **only when
required values are available**. It never includes plaintext caller values.
`change_classes` are presentation labels, not Run outcomes or Enforcer facts.
`original_root_sha256` and `proposed_root_sha256` identify Plan text only;
`imported_sources` records observed imported sources, not a certified closure
fingerprint. `closure_digest: null` prevents an incorrect authority claim.

A blocked/missing/invalid reference is rejected before any work. Missing required
caller values yield `UNRESOLVED_REQUIRED_INPUTS`, not an invented PASS. Converting
this proposal into a real Run requires the existing authorized Fleet admission,
original immutable source and caller file custody. Never run draft output through
a new runner or independent value parser.

## Evidence / boundaries

UX-C compiler tests, canonical source graph, caller values-file round-trip, source
identity, and negative edit tests establish **template-authoring correctness only**.
They do **not** establish external signer and roots, physical LACE absence, Gate A/B/C,
APP PASS or release certification.
