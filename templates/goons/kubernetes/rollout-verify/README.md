# kubernetes/rollout-verify

Verifies that one Deployment reaches the exact generation and immutable image produced by the preceding set-image Goon.

**Status:** runnable with prerequisites. **Effects:** Reads rollout state for an exact expected generation and image.

**Prerequisites:** Exact producer generation/image inputs, API target and declared host token/CA bindings.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Actual rollout verification; unavailable API cannot establish readiness.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

Select the exact API hostname, namespace, Deployment and container. Supply the declared token and CA certificate through host-backed environment authority. Image updates require a digest-qualified image; rollout verification consumes the exact expected generation and image. Inspect the Run for the actual mutation/verification result.

The `deployment` input uses the shared `kubernetes_deployment_name` DNS-subdomain type; `namespace` and `container` remain single-label `kubernetes_name` inputs. `expected_generation` and `expected_image` are runtime-produced values forwarded explicitly by the composing Goon. Their producer/consumer links are validated structurally before work; their final values are checked by typed Kubernetes execution after the producing Step runs.
