# python/profile

Profile the selected project’s real pytest workload with Pyinstrument.

## Before you run

The selected locked project/group must provide the commands used by the chosen Goons.

## Use this Plan

```sh
oh-goon templates show python/profile --kind plan
oh-goon plan inputs templates/plans/python/profile/plan.yml
```

Copy with `oh-goon templates show python/profile --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

## Configure a copy

Save the selected project and locked tool group as `values.yml`:

```yaml
inputs:
  project: .
  dependency_group: dev
```

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same selection when submitting the Plan. The project command’s own configuration remains authoritative.
