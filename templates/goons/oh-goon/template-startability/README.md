# oh-goon/template-startability

Run the catalog-wide shipped-template startability proof through the repository-owned certification harness.

## Use this Goon

```sh
oh-goon templates show oh-goon/template-startability --kind goon
oh-goon plan inputs templates/goons/oh-goon/template-startability/goon.yml
```

Use `goons: [oh-goon/template-startability]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

The harness discovers the shipped Plan catalog through `PlanTemplateStore`, resolves every Plan through the canonical production resolver, binds the smallest declared real repository fixture, proves required native/project execution authority, and drives production admission only through `RUN_ADMITTED`.

It must stop before Run/Step workload execution. This Goon delegates to the focused repository target; `make certify` remains the release-critical authority because the canonical APP certification Plan orders this Goon.
