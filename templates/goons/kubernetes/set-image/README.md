# kubernetes/set-image

Commit one immutable OCI digest reference to exactly one container in one Kubernetes apps/v1 Deployment. Fleet talks directly to one declared API endpoint with attempt-private bearer-token/CA authority, applies a resourceVersion/current-image JSON Patch, and records only the committed spec transition. It does not read kubeconfig and does not wait for rollout.

**Status:** runnable with prerequisites. **Effects:** Mutates one exact Deployment container image using optimistic tests.

**Prerequisites:** A digest-qualified image, exact API/namespace/Deployment/container and declared host token/CA bindings.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** `KUBERNETES_RESOURCE_VERSION`, `KUBERNETES_GENERATION`, `KUBERNETES_IMAGE`; mutation alone makes no readiness claim.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

Select the exact API hostname, namespace, Deployment and container. Supply the declared token and CA certificate through host-backed environment authority. Image updates require a digest-qualified image; rollout verification consumes the exact expected generation and image. Inspect the Run for the actual mutation/verification result.

## Contract

- Canonical identity: `kubernetes/set-image`.
- Repository identity is enforced by the CI/CD execution target; this Goon adds only capability-specific requirements.
- Inputs: `kubernetes_api_hostname`, `namespace`, `deployment`, `container`, `image`.
- Input authority: `deployment` is a Kubernetes DNS-subdomain resource name (`kubernetes_deployment_name`); namespace and container are DNS labels (`kubernetes_name`). The Plan projects these types from this Goon rather than restating schemas.
- Steps: `commit`.
- Public outputs: `KUBERNETES_RESOURCE_VERSION`, `KUBERNETES_GENERATION`, `KUBERNETES_IMAGE`.

## Safety boundary

The image input must be a full registry/repository reference qualified by `@sha256:<64 lowercase hex>`; mutable tags are rejected before API access. Kubernetes mutation uses the typed direct API contract with explicit endpoint, namespace, Deployment/container identity, bearer-token/CA sources, TLS verification, and optimistic preconditions. It does not consume ambient kubeconfig.

Machine-readable truth is [`goon.yml`](./goon.yml). The README explains intent and safety only; it is never runtime authority.
