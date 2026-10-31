# kubernetes/set-image

Commit one immutable OCI digest reference to exactly one container in one Kubernetes apps/v1 Deployment. Fleet talks directly to one declared API endpoint with attempt-private bearer-token/CA authority, applies a resourceVersion/current-image JSON Patch, and records only the committed spec transition. It does not read kubeconfig and does not wait for rollout.

**Status:** runnable with prerequisites. **Effects:** Mutates one exact Deployment container image using optimistic tests.

**Prerequisites:** A digest-qualified image, exact API/namespace/Deployment/container and declared host token/CA bindings.

```sh
oh-goon init --template kubernetes/set-image --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

The [kubernetes/set-image Plan](../../../plans/kubernetes/set-image/README.md) supplies the surrounding policy and ordered work. Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `KUBERNETES_RESOURCE_VERSION`, `KUBERNETES_GENERATION`, `KUBERNETES_IMAGE`; mutation alone makes no readiness claim.

Select the exact API hostname, namespace, Deployment and container. Supply the declared token and CA certificate through host-backed environment authority. Image updates require a digest-qualified image; rollout verification consumes the exact expected generation and image. Inspect the Run for the actual mutation/verification result.

## Safety boundary

The image input must be a full registry/repository reference qualified by `@sha256:<64 lowercase hex>`; mutable tags are rejected before API access. Kubernetes mutation uses the typed direct API contract with explicit endpoint, namespace, Deployment/container identity, bearer-token/CA sources, TLS verification, and optimistic preconditions. It does not consume ambient kubeconfig.
