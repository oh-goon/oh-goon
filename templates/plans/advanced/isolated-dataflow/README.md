# advanced/isolated-dataflow

**Teaching example. Not a production baseline. Plan.**

Selects the isolated-dataflow teaching Goon and exposes its inputs at the Plan boundary.

This package intentionally favors readability over a complete production policy. Use it to learn the Plan/Goon authoring model; do not copy it into production without adding the domain-specific admission, resource, secret, network, evidence, and release controls required by that workload.

Machine-readable truth is [`plan.yml`](./plan.yml).
