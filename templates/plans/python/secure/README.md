# python/secure

Run Python quality, then the native dependency and repository vulnerability scanners. The scanner report retention requires the configured strict content store.

## Before you run

The selected locked project/group must provide the commands used by the chosen Goons.

## Use this Plan

```sh
oh-goon templates show python/secure --kind plan
oh-goon plan inputs templates/plans/python/secure/plan.yml
```

Copy with `oh-goon templates show python/secure --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

## Configure a copy

Save these choices as `values.yml`. Omitted optional inputs keep their Goon defaults; native project configuration still owns test and lint policy.

```yaml
inputs:
  typechecker: ty
```

Validate and lock the same selection:

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same file and any explicit `--input` overrides when running. An override also needs a matching refreshed lock. Inspect the returned Run with `plan status`, `plan steps` and `plan outputs`.
