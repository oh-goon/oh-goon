# oci/tag-gate

**Production template. Production Plan.**

Read-only deployment/release gate for an existing OCI tag. The plan succeeds only when the mutable tag currently resolves to the exact expected sha256 digest. It never mutates registry state; promotion remains unsupported until an atomic compare-and-set registry authority exists.

## Contract

- Canonical identity: `oci/tag-gate`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `registry_hostname`, `repository`, `tag`, `expected_digest`.
- Ordered Goons: `oci/tag-assert`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.
