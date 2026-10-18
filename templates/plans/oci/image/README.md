# oci/image

Standalone typed OCI image build for an admitted Python/uv repository. Buildah runs in Fleet isolation, external base pulls are limited to one declared TLS registry, and Containerfile RUN instructions have no network. Output is a retained OCI archive plus deterministic build evidence; nothing is pushed.

## Use this Plan

```sh
oh-goon templates show oci/image --kind plan
oh-goon plan inputs templates/plans/oci/image/plan.yml
```

Copy with `oh-goon templates show oci/image --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

Prepare the selected OCI execution provider and registry access before execution. Base-image pull and destination publication registries are independent inputs. Credentials use the declared host-backed environment sources, never values files. Inspect digest/workspace outputs through the Run; publishing and retained bytes require their configured owners.

## Configure a copy

Save the following illustrative values as `values.yml`. Replace example infrastructure addresses and targets with your own before submitting work. Credentials remain in the declared environment bindings.

```yaml
inputs:
  base_registry_hostname: registry.example.com
  containerfile: Dockerfile
  context: .
  platform: linux/amd64
```

Validate and lock the same selection:

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same file and any explicit `--input` overrides when running. An override also needs a matching refreshed lock. Inspect the returned Run with `plan status`, `plan steps` and `plan outputs`.


## OCI registry and caller-choice contract

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The Plan projects caller choices only; typed Goon schemas and explicit producer
bindings remain the machine authority. Never infer the publication registry from
the base-image pull registry.
