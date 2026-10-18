# oh-goon/app-certification

Canonical final APP verification for one exact initialized repository revision.

## Use this Plan

```sh
oh-goon templates show oh-goon/app-certification --kind plan
oh-goon plan inputs templates/plans/oh-goon/app-certification/plan.yml
```

Copy with `oh-goon templates show oh-goon/app-certification --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

`make init` is the single normal dependency/interpreter materialization boundary. This Plan has `execution.purpose: verification` and consumes the initialized repository directly; it must not recreate `.venv`, rerun `uv sync`/`npm ci`, select another interpreter, or reacquire already-present packages.

The Plan is intentionally small and proves only semantic APP authority:

1. P0 execution/security and generated-contract freshness;
2. semantic repository architecture/ownership boundaries;
3. exact shipped-template catalog startability through canonical fixtures and production admission, stopping before Step work;
4. frontend functional readiness; and
5. deterministic local runtime start/probe/stop.

Fresh bootstrap/release reproducibility and broad backend/tooling/integration/P1/browser diagnostics are separate owners.

## Validate your copy

```sh
oh-goon plan validate plan.yml
oh-goon lock plan.yml
```
