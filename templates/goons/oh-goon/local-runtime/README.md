# oh-goon/local-runtime

Runs the canonical local-runtime APP verification path against the already initialized
checkout. It starts the real backend and Vite frontend, proves local-access authentication
and browser-facing proxy health, then stops both processes and proves the loopback ports are
released.

## Use this Goon

```sh
oh-goon templates show oh-goon/local-runtime --kind goon
oh-goon plan inputs templates/goons/oh-goon/local-runtime/goon.yml
```

Use `goons: [oh-goon/local-runtime]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This Goon runs under `execution.purpose: verification`. It does **not** acquire, inherit,
materialize, validate, or require strict Fleet executor inventory, cgroup-v2 delegation, or
systemd `Delegate=yes` authority. Production-shaped strict Fleet execution, physical Step
cancellation, crash/restart recovery, and strict parallel Shared Plan behavior remain owned by
`make strict-local-runtime-acceptance`, `make test-real-world`, and
`make test-real-world-parallel`.

This is an Oh-Goon repository verification capability. The Goon owns orchestration/evidence
only and does not redefine either the local-launch owner or strict release/CI execution.
