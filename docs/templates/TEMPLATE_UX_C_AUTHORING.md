# UX-C — Modular Plan authoring (development, read-only)

UX-C proposes ordinary Plan text. It never creates a Run, changes a Schedule,
saves executable source, or controls Fleet admission. The existing Plan compiler,
imported Goon source graph and typed binder retain all executable authority.
Normal operator setup uses one [run configuration](../development/TEMPLATE_INPUT_INSPECTION.md).

## Discover and compose

`oh-goon-template-ux-c-draft --catalog` projects the existing catalog
classification and descriptions. A Plan digest is an optimistic source-revision
check; it is not a closure digest or execution permission. For a development
source-editing proposal:

```json
{
  "edits": [{"op": "move", "from": 1, "to": 0}],
  "values": {"typechecker": "ty"}
}
```

```sh
# Obtain ROOT_SHA256 by inspecting this checkout's exact Plan source.
uv run --project backend --frozen oh-goon-template-ux-c-draft \
  --plan python/standard --expect-sha256 "$ROOT_SHA256" \
  --request /tmp/team-request.json --output /tmp/draft-report.json
```

The response's `plan_yaml` is proposed text. `report` describes the comparison.
Any scalar value draft in this diagnostic response is data for reviewing the
proposed contract; it is not an accepted separate values-file execution route.
Put desired caller values in the selected `run.yml` before validation and lock.
Keep credentials out of source, input mappings and diagnostic reports.

Operations address zero-based import occurrences: `add` (`at`, `import`),
`remove` (`at`), `move` (`from`, `to`), and `replace` (`at`, `import`). An import
mapping may contain normal `import`, `as`, `pass`, `with` or `failure_impact`.
It cannot contain executable Steps, a new interpreter or runtime reviewer list.
The original compiler validates alias collisions and producer/input references.
Unique unbound public inputs follow the edited imports. Optional
`projected_inputs` narrows that interface; Atomic Goons retain definitions,
types, defaults and constraints. Editing references never reorders a Plan to
rescue invalid dependencies.

## Inspect and execute separately

`report.before` and `report.after` list compiled import identities and order.
`resolved_comparison` uses the original resolver and reports Step identities,
order and capability hashes only when required values are available. Missing
required values mean `UNRESOLVED_REQUIRED_INPUTS`, not approval. Presentation
labels are not Run outcomes or Enforcer facts. Source SHA values identify Plan
text; `closure_digest: null` prevents claiming a certified closure.

A chosen project-owned Plan must be saved explicitly under its authoring root,
selected with `source: {kind: file-plan, path: ./plan.yml}` in `run.yml`, and
validated/locked through the existing owners. A draft never authorizes a Run,
changes an accepted Schedule, installs a provider, or creates a second executor.

Compiler, source-graph, identity and adverse-edit regressions establish source
authoring behavior only. Real native execution, cancellation, external effects,
APP and release qualification require independent evidence. Human and agent
productivity improvement remains unmeasured without controlled paired trials.
