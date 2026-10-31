# UX-D — Reproducible authoring evaluation and distribution audit

UX-D checks real shipped template sources without admitting, saving or executing a
Plan. It is a **development evaluation**, not a new template parser, validator,
Fleet entrypoint, Enforcer fact owner, or certification authority.

## Current operator workflow

Normal setup uses `init`, one `run.yml`, `workflow inspect`,
`workflow validate`, `lock` and `run`. See the
[current configuration guide](../development/TEMPLATE_INPUT_INSPECTION.md).
The no-op UX-C proposal service below is a source-authoring diagnostic; it
never creates a second executable configuration route or grants effects.

See [the Atomic/Composite contract](../../templates/README.md),
[Goon authoring](../development/GOON_AUTHORING.md),
[input resolution and custody](../development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md),
and [UX-C Plan drafts](TEMPLATE_UX_C_AUTHORING.md).

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

## Historical UX-D decision gates

These conditions describe the original UX-D evaluation, whose source had 41
Goons and 24 Plans. They are not the current census or current CLI contract.
Current counts are derived from the exact checkout by the inventory owner.

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

## Current clean-break acceptance evidence

The selected configuration interface replaces old public Plan/input/root/agent
flag assembly. Old schemas and removed commands refuse; no migration or dual
reader is required. Historical Run journals and Game Tape retain their recorded
meaning. Shorter configuration is not evidence of better human performance.

| Requirement family | Source and focused proof owner | Qualification limit |
| --- | --- | --- |
| CAT-01/02/05/06/07/08, AT-01/18/19/26/27/36 | Canonical template stores, `template_ux_a_inventory`, template/example READMEs and census regressions | Every current package/example enumerated; source inspection does not prove an executed workload. |
| CFG-01–11, CUT-01/02, AT-03–07/14/15/29/32 | Captured run selection, original compiler/binder/lock/Fleet and CLI regressions | Validation, frozen identity and fresh effect admission are distinct. |
| AGT-01/03/04/05/06, UX-04–10 | Original Skill registry, agent provider/native owners, bounded repair and configuration projection | Configured, installed, authenticated, connected, contained and admitted remain distinct states. |
| UX-01/02/03, AT-03/17 | Existing template authoring owner and `init` regressions | Destination refusal and complete publication do not authorize execution. |
| CUT-03/04, AT-06/07/15/16/31 | Original target freshness, supervisor, cancellation, Journal and Game Tape tests | Real native/external qualification must be run separately and remains unverified when unavailable. |
| CAT-03/04, CUT-05 | Root/quickstart/configuration guides plus all shipped package and example READMEs | Copyable commands must match the implemented CLI; placeholders do not represent live services. |
| AGT-02, AT-08–11 | Concrete project-owned Composite authoring | Custom reviewer generation needs its own compiler and publication proof; no runtime reviewer interpreter. |
| AT-28/33/34/35, G4 | Fresh Linux/WSL first-use and controlled blind human/agent trials across all eight categories | **NOT MEASURED** here. No completion-rate/time/edit improvement or 20% claim is established. |

This maps evidence to existing owners; it is not an additional runtime gate or
a report that those checks passed. The task completion response records the
actual checks executed and their results. Existing architecture, backend,
frontend, APP and release owners retain their independent qualification gates.
