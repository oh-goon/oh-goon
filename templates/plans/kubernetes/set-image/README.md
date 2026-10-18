# kubernetes/set-image

**Production template. Production Plan.**

Standalone immutable Kubernetes Deployment image commit. The requested image must already be digest-qualified. This changes one Deployment pod-template image with optimistic JSON Patch tests and intentionally makes no rollout-readiness claim.

## Contract

- Canonical identity: `kubernetes/set-image`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `kubernetes_api_hostname`, `namespace`, `deployment`, `container`, `image`.
- Ordered Goons: `kubernetes/set-image`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.
