# python/secure

**Production template. Production Plan.**

Production Python quality and vulnerability validation using the standard quality capability plus the reusable combined CVE capability. Quality policy is explicit at the Plan boundary; security severity is intentionally not caller-lowerable.

## Contract

- Canonical identity: `python/secure`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `coverage_threshold`.
- Ordered Goons: `python/quality`, `security/cve-combined`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.
