# python/dependency-inventory

Observe repository dependency manifests without installing dependencies.

## Before you run

The selected locked project/group must provide the commands used by the chosen Goons.

## Use this Plan

```sh
oh-goon templates show python/dependency-inventory --kind plan
oh-goon plan inputs templates/plans/python/dependency-inventory/plan.yml
```

Copy with `oh-goon templates show python/dependency-inventory --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

Inspection Plan that selects the canonical `python/dependency-inventory` Goon. Use it through the existing Shared Run or Schedule paths to refresh dependency evidence for an explicit repository population.

The Plan only observes. Fleet filtering and later maintenance execution remain separate user actions; a dependency query never silently starts this Plan or any mutation.

## Validate your copy

```sh
oh-goon plan validate plan.yml
oh-goon lock plan.yml
```
