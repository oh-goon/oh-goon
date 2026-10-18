# python/uv-audit

Audit the repository-root uv lock with native uv’s vulnerability audit.

## Before you run

The selected locked project/group must provide the commands used by the chosen Goons.

## Use this Plan

```sh
oh-goon templates show python/uv-audit --kind plan
oh-goon plan inputs templates/plans/python/uv-audit/plan.yml
```

Copy with `oh-goon templates show python/uv-audit --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

## Validate your copy

```sh
oh-goon plan validate plan.yml
oh-goon lock plan.yml
```
