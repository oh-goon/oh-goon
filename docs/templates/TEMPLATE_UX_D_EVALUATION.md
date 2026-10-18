# UX-D — Reproducible authoring evaluation and distribution audit

UX-D checks real shipped template sources without admitting, saving or executing a
Plan. It is a **development evaluation**, not a new template parser, validator,
Fleet entrypoint, Enforcer fact owner, or certification authority.

## One ordinary authoring workflow

1. **Discover.** Browse the existing catalog via `oh-goon-template-ux-c-draft --catalog`.
2. **Compose.** Select a shipped Plan revision; create a JSON proposal with only
   ordered `add`, `remove`, `move` or `replace` operations. An import mapping may
   name an individual occurrence with `as:`. Every Goon is still imported via
   its canonical category/name identity, and Plans never contain executable Steps.
3. **Configure.** Select only known Plan input names. Generate independent
   team values-file drafts, without overlays, secret interpolation, inheritance
   or any Schedule re-read.
4. **Inspect.** Use the original resolver-backed comparison to identify changed
   imports, resolved Step order/identity, recipients and capability contracts.
   Missing required values mean `UNRESOLVED_REQUIRED_INPUTS`, not approval.
5. **Validate.** The original compiler and input binder reject malformed
   names, bindings, source identities and typed values before any work.
6. **Run separately.** Only the existing Fleet admission can
   schedule or execute the result. A draft does not save source, mutate an
   immutable Schedule or authorize a Run.

See [the Atomic/Composite input contract](../../templates/README.md),
[Goon authoring and occurrence names](../development/GOON_AUTHORING.md),
[input resolution and caller-file custody](../development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md),
[UX-C Plan drafts](TEMPLATE_UX_C_AUTHORING.md), and
[canonical-source input journeys](../development/TEMPLATE_INPUT_INSPECTION.md#evaluation-and-proof-scope).

## Reproduce the scripted evidence

From a fresh exact-source checkout with the locked project dependencies present:

```sh
uv run --offline --frozen --no-sync --project backend oh-goon-template-ux-d-evidence \
  --root . --output ../UX_D_EVIDENCE.json
```

To check standalone source packaging, also specify
`--source-archive ../OH_GOON_SOURCE.zip` with the actual source archive path. The tool reports every
shipped Plan and Goon using **the same canonical template stores**. It checks
that an in-memory UX-C no-op proposal retains the actual compiled Plan and
never acquires save/execute permission. It audits local reference-guide links
and mandatory guide membership of the source ZIP. It does **not** replace
`make qualify-development-wsl`, `make check`, `make verify`, or `make certify`.

The generated JSON is diagnostic-only, deterministic, and omits caller values.
It distinguishes a root-file digest from the imported source closure (which
this authoring report does **not** certify). The report's mechanical counts
are **scripted measurements**, not observations of human productivity.

## Frozen UX-D decision gates

- **100% catalog/contract agreement** on the checked-in 41 Goons and 24 Plans,
  no broken relative links, and no additional writer/dispatcher.
- **No auto-published outputs**; optional wildcard/public-output shortcut
  remains **NO-GO** until a real evaluation establishes a >=25% reduction in
  repetitive bindings without any exposure expansion.
- **No implicit shell** in `run:`. `uses:` retains its typed authority.
- **At least 20% median improvement** in task time or edits on paired,
  controlled, real human/agent trials would justify the optional UX changes;
  without such trials, status is **NOT MEASURED**, not PASS.
- Scripted HTTP journeys and browser fixtures are distinct from real backend
  HTTP and native browser execution. TestClient is not a browser witness.
- Native WSL, selected backend/custody guarantees, terminal truth, Game Tape,
  APP certification, and release qualification require their own evidence;
  this authoring evaluation makes no readiness claim for them.

**No version-1 template migration is included just for appearance.** Existing
aliases, Steps, output mappings, values-file and Schedule semantics are preserved.
