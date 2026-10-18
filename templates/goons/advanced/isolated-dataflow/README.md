# advanced/isolated-dataflow

**Teaching example. Not a production baseline. Atomic Goon.**

Demonstrates explicit inputs, step outputs, env/variable derivation, and per-step imports without ambient shell state.

This package intentionally favors readability over a complete production policy. Use it to learn the Plan/Goon authoring model; do not copy it into production without adding the domain-specific admission, resource, secret, network, evidence, and release controls required by that workload.

Machine-readable truth is [`goon.yml`](./goon.yml).
