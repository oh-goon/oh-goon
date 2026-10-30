# kubernetes/rollout-verify

Verifies that one Deployment reaches the exact generation and immutable image produced by the preceding set-image Goon.

**Status:** runnable with prerequisites. **Effects:** Reads rollout state for an exact expected generation and image.

**Prerequisites:** Exact producer generation/image inputs, API target and declared host token/CA bindings.

```sh
oh-goon init --template kubernetes/rollout-verify --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

This typed operation requires strict CI/CD execution. Add the explicit policy to the generated `workflow/plan.yml`:

```yaml
execution: {purpose: cicd}
```

Configure the admitted strict executor before running; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md). For a full build/publish/deploy workflow, use the [release-deploy Plan](../../../plans/oci/release-deploy/README.md).

**Expected outputs:** Actual rollout verification; unavailable API cannot establish readiness.

Select the exact API hostname, namespace, Deployment and container. Supply the declared token and CA certificate through host-backed environment authority. Image updates require a digest-qualified image; rollout verification consumes the exact expected generation and image. Inspect the Run for the actual mutation/verification result.

The `deployment` input uses the shared `kubernetes_deployment_name` DNS-subdomain type; `namespace` and `container` remain single-label `kubernetes_name` inputs. `expected_generation` and `expected_image` are runtime-produced values forwarded explicitly by the composing Goon. Their producer/consumer links are validated structurally before work; their final values are checked by typed Kubernetes execution after the producing Step runs.
