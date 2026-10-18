# kubernetes/set-image

Standalone immutable Kubernetes Deployment image commit. The requested image must already be digest-qualified. This changes one Deployment pod-template image with optimistic JSON Patch tests and intentionally makes no rollout-readiness claim.

## Use this Plan

```sh
oh-goon templates show kubernetes/set-image --kind plan
oh-goon plan inputs templates/plans/kubernetes/set-image/plan.yml
```

Copy with `oh-goon templates show kubernetes/set-image --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

Select the exact API hostname, namespace, Deployment and container. Supply the declared token and CA certificate through host-backed environment authority. Image updates require a digest-qualified image; rollout verification consumes the exact expected generation and image. Inspect the Run for the actual mutation/verification result.

## Configure a copy

Save the following illustrative values as `values.yml`. Replace example infrastructure addresses and targets with your own before submitting work. Credentials remain in the declared environment bindings.

```yaml
inputs:
  kubernetes_api_hostname: kube.example.com
  namespace: production
  deployment: api
  container: api
  image: harbor.example.com/team/app@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Validate and lock the same selection:

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same file and any explicit `--input` overrides when running. An override also needs a matching refreshed lock. Inspect the returned Run with `plan status`, `plan steps` and `plan outputs`.
