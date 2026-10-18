# kubernetes/rollout-verify

Verifies that one Deployment reaches the exact generation and immutable image produced by the preceding set-image Goon.

The `deployment` input uses the shared `kubernetes_deployment_name` DNS-subdomain type; `namespace` and `container` remain single-label `kubernetes_name` inputs. `expected_generation` and `expected_image` are runtime-produced values forwarded explicitly by the composing Goon. Their producer/consumer links are validated structurally before work; their final values are checked by typed Kubernetes execution after the producing Step runs.
