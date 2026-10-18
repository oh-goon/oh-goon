# oci/release-candidate

**Production template. Production Plan.**

Quality-gated OCI release candidate: run the strict Python quality pipeline, then build one retained OCI archive through the typed image-builder boundary. The plan never pushes to a registry and imports no registry credentials.

## Contract

- Canonical identity: `oci/release-candidate`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `base_registry_hostname`, `containerfile`, `context`, `platform`, `coverage_threshold`.
- Ordered Goons: `python/quality`, `oci/image`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.

## OCI registry and caller-choice contract

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The Plan projects caller choices only; typed Goon schemas and explicit producer
bindings remain the machine authority. Never infer the publication registry from
the base-image pull registry.
