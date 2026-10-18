# oh-goon/browser-localhost-transport-preflight

Fail closed before browser qualification unless the same Node TCP stack used by Playwright can connect to a live IPv4 localhost endpoint and promptly observe `ECONNREFUSED` after that exact endpoint closes.

## Use this Goon

```sh
oh-goon templates show oh-goon/browser-localhost-transport-preflight --kind goon
oh-goon plan inputs templates/goons/oh-goon/browser-localhost-transport-preflight/goon.yml
```

Use `goons: [oh-goon/browser-localhost-transport-preflight]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

The probe allocates an OS-selected ephemeral port. It does not hard-code Vite ports, inspect WSL routing internals, or require a particular WSL networking mode. WSL mirrored networking is diagnostic guidance only when the observable localhost transport contract fails.

This proves **Node localhost TCP connect/closed-endpoint refusal**, not browser
engine launch, harness listing or runtime port ownership. The other browser
preflights invoke the transport check through native npm prerequisites, but this
remains an independently identifiable failure boundary. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
