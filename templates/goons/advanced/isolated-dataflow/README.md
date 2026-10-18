# advanced/isolated-dataflow

Demonstrate explicit inputs, native command outputs and scoped environment imports in a sequential project workflow.

**Teaching example. Not a production baseline. Atomic Goon.**

## Use this Goon

```sh
oh-goon templates show advanced/isolated-dataflow --kind goon
oh-goon plan inputs templates/goons/advanced/isolated-dataflow/goon.yml
```

Use `goons: [advanced/isolated-dataflow]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This teaching workflow requires the project-owned preparation/publication scripts and locked tools illustrated by the existing certification fixture. Its declared Step outputs and environment imports are explicit dataflow; native failure remains authoritative.

Demonstrates explicit inputs, step outputs, env/variable derivation, and per-step imports without ambient shell state.

This package intentionally favors readability over a complete production policy. Use it to learn the Plan/Goon authoring model; do not copy it into production without adding the domain-specific admission, resource, secret, network, evidence, and release controls required by that workload.

Machine-readable truth is [`goon.yml`](./goon.yml).
