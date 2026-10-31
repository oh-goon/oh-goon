# kubernetes/release

Commit one immutable OCI digest reference to exactly one apps/v1 Deployment container, then explicitly verify that exact committed Deployment generation reaches readiness. Mutation and readiness remain two separate steps and two separate canonical facts.

**Status:** runnable with prerequisites. **Effects:** Mutates one container image then verifies that exact resulting generation/image.

**Prerequisites:** A digest-qualified image, exact target and declared host token/CA bindings.

```sh
oh-goon init --template kubernetes/release --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

This typed operation requires strict CI/CD execution. Add the explicit policy to the generated `workflow/plan.yml`:

```yaml
execution: {purpose: cicd}
```

Configure the admitted strict executor before running; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md). For a full build/publish/deploy workflow, use the [release-deploy Plan](../../../plans/oci/release-deploy/README.md).

**Expected outputs:** Set-image resource/generation/image outputs and subsequent native rollout result.

Select the exact API hostname, namespace, Deployment and container. Supply the declared token and CA certificate through host-backed environment authority. Image updates require a digest-qualified image; rollout verification consumes the exact expected generation and image. Inspect the Run for the actual mutation/verification result.

## Safety boundary

The image input must be a full registry/repository reference qualified by `@sha256:<64 lowercase hex>`; mutable tags are rejected before API access. Kubernetes mutation uses the typed direct API contract with explicit endpoint, namespace, Deployment/container identity, bearer-token/CA sources, TLS verification, and optimistic preconditions. It does not consume ambient kubeconfig.

## Explicit recipient forwarding

```yaml
- import: kubernetes/set-image
  pass: [kubernetes_api_hostname, namespace, deployment, container]
- import: kubernetes/rollout-verify
  pass: [kubernetes_api_hostname, namespace, deployment, container]
  with:
    expected_generation: ${{ goons.kubernetes-set-image.outputs.KUBERNETES_GENERATION }}
    expected_image: ${{ goons.kubernetes-set-image.outputs.KUBERNETES_IMAGE }}
```

The shared four inputs have **two separately authored** recipient bindings;
`image` has one direct owner and reaches `set-image` without another binding.
The verification Goon consumes two
explicit producer-output references through `with:`. Typed child schemas,
outputs and the upstream Plan are unchanged.
