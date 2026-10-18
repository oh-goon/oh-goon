# oci/image

**Production template. Production Plan.**

Standalone typed OCI image build for an admitted Python/uv repository. Buildah runs in Fleet isolation, external base pulls are limited to one declared TLS registry, and Containerfile RUN instructions have no network. Output is a retained OCI archive plus deterministic build evidence; nothing is pushed.

## Contract

- Canonical identity: `oci/image`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `base_registry_hostname`, `containerfile`, `context`, `platform`.
- Ordered Goons: `oci/image`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.

## OCI registry and caller-choice contract

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The Plan projects caller choices only; typed Goon schemas and explicit producer
bindings remain the machine authority. Never infer the publication registry from
the base-image pull registry.
