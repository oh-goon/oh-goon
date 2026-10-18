# python/package-artifacts

**Production template. Production Plan.**

Strict standalone Python package build. This builds wheel/sdist artifacts only; it is intentionally not an OCI/Docker image build and does not publish to a registry.

## Contract

- Canonical identity: `python/package-artifacts`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: none.
- Ordered Goons: `python/package-artifacts`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.
