# advanced/isolated-dataflow

Select the isolated-dataflow Goon and expose its project label and test timeout to callers.

**Teaching example. Not a production baseline. Plan.**

## Use this Plan

```sh
oh-goon templates show advanced/isolated-dataflow --kind plan
oh-goon plan inputs templates/plans/advanced/isolated-dataflow/plan.yml
```

Copy with `oh-goon templates show advanced/isolated-dataflow --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

This teaching workflow requires the project-owned preparation/publication scripts and locked tools illustrated by the existing certification fixture. Its declared Step outputs and environment imports are explicit dataflow; native failure remains authoritative.

Selects the isolated-dataflow teaching Goon and exposes its inputs at the Plan boundary.

This package intentionally favors readability over a complete production policy. Use it to learn the Plan/Goon authoring model; do not copy it into production without adding the domain-specific admission, resource, secret, network, evidence, and release controls required by that workload.

Machine-readable truth is [`plan.yml`](./plan.yml).

## Configure a copy

Save the project label and test timeout as `values.yml`. The project commands consume these explicit values.

```yaml
inputs:
  tag: dev
  test_timeout_seconds: '600'
```

Validate and lock the same selection:

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same file and any explicit `--input` overrides when running. An override also needs a matching refreshed lock. Inspect the returned Run with `plan status`, `plan steps` and `plan outputs`.
