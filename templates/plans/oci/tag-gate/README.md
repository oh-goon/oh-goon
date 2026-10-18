# oci/tag-gate

Read-only deployment/release gate for an existing OCI tag. The plan succeeds only when the mutable tag currently resolves to the exact expected sha256 digest. It never mutates registry state; promotion remains unsupported until an atomic compare-and-set registry authority exists.

## Use this Plan

```sh
oh-goon templates show oci/tag-gate --kind plan
oh-goon plan inputs templates/plans/oci/tag-gate/plan.yml
```

Copy with `oh-goon templates show oci/tag-gate --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

Prepare the selected OCI execution provider and registry access before execution. Base-image pull and destination publication registries are independent inputs. Credentials use the declared host-backed environment sources, never values files. Inspect digest/workspace outputs through the Run; publishing and retained bytes require their configured owners.

## Configure a copy

Save the following illustrative values as `values.yml`. Replace example infrastructure addresses and targets with your own before submitting work. Credentials remain in the declared environment bindings.

```yaml
inputs:
  registry_hostname: harbor.example.com
  repository: team/app
  tag: production
  expected_digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Validate and lock the same selection:

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same file and any explicit `--input` overrides when running. An override also needs a matching refreshed lock. Inspect the returned Run with `plan status`, `plan steps` and `plan outputs`.
