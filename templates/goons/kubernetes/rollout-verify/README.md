# kubernetes/rollout-verify

Verifies that one Deployment reaches the exact generation and immutable image produced by the preceding set-image Goon.

## Use this Goon

```sh
oh-goon templates show kubernetes/rollout-verify --kind goon
oh-goon plan inputs templates/goons/kubernetes/rollout-verify/goon.yml
```

Use `goons: [kubernetes/rollout-verify]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

Select the exact API hostname, namespace, Deployment and container. Supply the declared token and CA certificate through host-backed environment authority. Image updates require a digest-qualified image; rollout verification consumes the exact expected generation and image. Inspect the Run for the actual mutation/verification result.

The `deployment` input uses the shared `kubernetes_deployment_name` DNS-subdomain type; `namespace` and `container` remain single-label `kubernetes_name` inputs. `expected_generation` and `expected_image` are runtime-produced values forwarded explicitly by the composing Goon. Their producer/consumer links are validated structurally before work; their final values are checked by typed Kubernetes execution after the producing Step runs.
