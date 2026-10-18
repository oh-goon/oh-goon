# oh-goon/browser-harness-preflight

Load the Playwright configuration and certification specs without running browser journeys, proving the harness is discoverable before runtime execution.

## Use this Goon

```sh
oh-goon templates show oh-goon/browser-harness-preflight --kind goon
oh-goon plan inputs templates/goons/oh-goon/browser-harness-preflight/goon.yml
```

Use `goons: [oh-goon/browser-harness-preflight]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This is an Oh-Goon repository verification capability. It delegates to the repository's existing canonical command; the Goon owns orchestration/evidence only and does not redefine the gate.

This checks **configuration/spec discovery** without browser journeys; it does
not certify engine launch or clean runtime listeners. The native npm target
owns its transport prerequisite. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
