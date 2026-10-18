# oh-goon/browser-runtime-preflight

Require clean browser/backend runtime port ownership and the canonical non-reuse browser runtime boundary.

## Use this Goon

```sh
oh-goon templates show oh-goon/browser-runtime-preflight --kind goon
oh-goon plan inputs templates/goons/oh-goon/browser-runtime-preflight/goon.yml
```

Use `goons: [oh-goon/browser-runtime-preflight]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This is an Oh-Goon repository verification capability. It delegates to the repository's existing canonical command; the Goon owns orchestration/evidence only and does not redefine the gate.

This checks **runtime port ownership and server non-reuse**, not browser engine
installation or Playwright spec discovery. Browser certification's native Make
targets own their runtime-preflight prerequisite. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
