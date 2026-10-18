# oci/release-deploy

**Production template. Production Plan.**

Strict quality-gated immutable deployment example: run Python quality, build and content-addressedly publish one OCI image, then commit that exact digest reference to one Kubernetes Deployment container, then explicitly verify that exact Deployment generation. No mutable image tag, kubeconfig, Helm/apply merge, or implicit rollout watcher exists.

## Contract

- Canonical identity: `oci/release-deploy`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `coverage_threshold`, `base_registry_hostname`, `publish_registry_hostname`, `publish_repository`, `containerfile`, `context`, `platform`, `kubernetes_api_hostname`, `namespace`, `deployment`, `container`.
- Ordered Goons: `python/quality`, `oci/publish-image`, `kubernetes/release`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.

## OCI registry and caller-choice contract

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The Plan projects caller choices only; typed Goon schemas and explicit producer
bindings remain the machine authority. Never infer the publication registry from
the base-image pull registry.
