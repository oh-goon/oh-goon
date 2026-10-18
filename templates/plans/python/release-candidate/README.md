# python/release-candidate

Calculate candidate metadata, run Python quality and retain the project’s distribution bundle. Building uses the project’s version; metadata calculation does not stamp a new version into source.

## Before you run

The selected locked project/group must provide the commands used by the chosen Goons. The project supplies `release-metadata`; choose its channel and revision explicitly. The project supplies `package-artifacts`; retained bundle bytes require configured strict execution and content-store publication.

## Use this Plan

```sh
oh-goon templates show python/release-candidate --kind plan
oh-goon plan inputs templates/plans/python/release-candidate/plan.yml
```

Copy with `oh-goon templates show python/release-candidate --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

## Configure a copy

The Plan routes `project` and `dependency_group` to metadata, quality and packaging because they are shared names. Their schemas and defaults stay in the Goons. Other unique public inputs are exposed automatically.

Save these choices as `values.yml`. Omitted optional inputs keep their Goon defaults; native project configuration still owns test and lint policy.

```yaml
inputs:
  release_channel: production
  revision: abc1234
  typechecker: ty
```

Validate and lock the same selection:

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same file and any explicit `--input` overrides when running. An override also needs a matching refreshed lock. Inspect the returned Run with `plan status`, `plan steps` and `plan outputs`.


## Project release policy

The project owns its release-metadata and packaging commands. Metadata outputs describe candidate values; this Plan does not change the committed project version. Adapt the project command when your release policy needs different behavior.
