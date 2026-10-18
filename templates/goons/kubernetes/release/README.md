# kubernetes/release

**Production template. Production Composite Goon.**

Commit one immutable OCI digest reference to exactly one apps/v1 Deployment container, then explicitly verify that exact committed Deployment generation reaches readiness. Mutation and readiness remain two separate steps and two separate canonical facts.

## Contract

- Canonical identity: `kubernetes/release`.
- Repository identity is enforced by the CI/CD execution target; this Goon adds only capability-specific requirements.
- Inputs: `kubernetes_api_hostname`, `namespace`, `deployment`, `container`, `image`.
- Steps: `commit`, `verify`.
- Public outputs: `KUBERNETES_RESOURCE_VERSION`, `KUBERNETES_GENERATION`, `KUBERNETES_IMAGE`.

## Safety boundary

The image input must be a full registry/repository reference qualified by `@sha256:<64 lowercase hex>`; mutable tags are rejected before API access. Kubernetes mutation uses the typed direct API contract with explicit endpoint, namespace, Deployment/container identity, bearer-token/CA sources, TLS verification, and optimistic preconditions. It does not consume ambient kubeconfig.

Machine-readable truth is [`goon.yml`](./goon.yml). The README explains intent and safety only; it is never runtime authority.

## Explicit recipient forwarding

```yaml
- import: kubernetes/set-image
  pass: [kubernetes_api_hostname, namespace, deployment, container, image]
- import: kubernetes/rollout-verify
  pass: [kubernetes_api_hostname, namespace, deployment, container]
  with:
    expected_generation: ${{ goons.kubernetes-set-image.outputs.KUBERNETES_GENERATION }}
    expected_image: ${{ goons.kubernetes-set-image.outputs.KUBERNETES_IMAGE }}
```

The shared four inputs have **two separately authored** recipient bindings;
`image` reaches only `set-image`. The verification Goon still consumes two
explicit producer-output references through `with:`. Typed child schemas,
outputs and the upstream Plan are unchanged.
