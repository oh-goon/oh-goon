# python/quality

Run formatting, lint, one selected type checker and tests using native project configuration. Start here for an ordinary local Python workflow.

## Before you run

The selected locked project/group must provide the commands used by the chosen Goons.

## Use this Plan

```sh
oh-goon templates show python/quality --kind plan
oh-goon plan inputs templates/plans/python/quality/plan.yml
```

Copy with `oh-goon templates show python/quality --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

## Configure a copy

The default Plan is just:

```yaml
version: 1
goons:
- python/quality
```

Its public inputs are available automatically; the Plan repeats no input declarations or forwarding. For a fixed override, replace the plain import with `import: python/quality` and `with: {typechecker: ty}`. Fixed bindings are excluded from automatic caller overrides. To choose values per run, keep the plain import and use caller values below.

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
