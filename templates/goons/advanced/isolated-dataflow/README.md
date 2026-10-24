# advanced/isolated-dataflow

Demonstrate explicit inputs, native command outputs and scoped environment imports in a sequential project workflow.

**Status:** teaching example. **Effects:** Runs project scripts/tests/build and can publish.

**Prerequisites:** Project-owned preparation/publication scripts, the locked native uv/test/build tools and the declared host token. This is a teaching workflow; the project scripts can publish.

```sh
oh-goon templates init advanced/isolated-dataflow --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** The prepare Step publishes `RELEASE_TAG`; following commands consume explicit output/environment bindings.

**Teaching example. Not a production baseline. Atomic Goon.**

This teaching workflow requires the project-owned preparation/publication scripts and locked tools illustrated by the existing certification fixture. Its declared Step outputs and environment imports are explicit dataflow; native failure remains authoritative.

Demonstrates explicit inputs, step outputs, env/variable derivation, and per-step imports without ambient shell state.

This package intentionally favors readability over a complete production policy. Use it to learn the Plan/Goon authoring model; do not copy it into production without adding the domain-specific admission, resource, secret, network, evidence, and release controls required by that workload.
