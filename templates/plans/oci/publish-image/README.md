# oci/publish-image

**Production template. Production Plan.**

Standalone production build-and-publish Plan. The image is built as a retained OCI archive and published only to its digest-qualified registry reference. OCI_REGISTRY_USERNAME and OCI_REGISTRY_PASSWORD are required host-backed credential sources; no mutable tag is created.

## Contract

- Canonical identity: `oci/publish-image`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `base_registry_hostname`, `publish_registry_hostname`, `publish_repository`, `containerfile`, `context`, `platform`.
- Ordered Goons: `oci/publish-image`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.

## OCI registry and caller-choice contract

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The Plan projects caller choices only; typed Goon schemas and explicit producer
bindings remain the machine authority. Never infer the publication registry from
the base-image pull registry.
