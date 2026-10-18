# kubernetes/set-image

Commit one immutable OCI digest reference to exactly one container in one Kubernetes apps/v1 Deployment. Fleet talks directly to one declared API endpoint with attempt-private bearer-token/CA authority, applies a resourceVersion/current-image JSON Patch, and records only the committed spec transition. It does not read kubeconfig and does not wait for rollout.

## Use this Goon

```sh
oh-goon templates show kubernetes/set-image --kind goon
oh-goon plan inputs templates/goons/kubernetes/set-image/goon.yml
```

Use `goons: [kubernetes/set-image]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

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
