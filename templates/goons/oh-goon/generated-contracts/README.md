# oh-goon/generated-contracts

Verify generated OpenAPI/frontend contracts are byte-current without rewriting authored or generated source.

## Use this Goon

```sh
oh-goon templates show oh-goon/generated-contracts --kind goon
oh-goon plan inputs templates/goons/oh-goon/generated-contracts/goon.yml
```

Use `goons: [oh-goon/generated-contracts]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This is an Oh-Goon repository verification capability. It delegates to the repository's existing canonical command; the Goon owns orchestration/evidence only and does not redefine the gate.
