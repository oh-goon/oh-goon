# advanced/isolated-dataflow

Demonstrate explicit inputs, native command outputs and scoped environment imports in a sequential project workflow.

**Status:** teaching example. **Effects:** Runs project scripts/tests/build and can publish.

**Prerequisites:** Project-owned preparation/publication scripts, the locked native uv/test/build tools and the declared host token. This is a teaching workflow; the project scripts can publish.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** The prepare Step publishes `RELEASE_TAG`; following commands consume explicit output/environment bindings.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

**Teaching example. Not a production baseline. Atomic Goon.**

This teaching workflow requires the project-owned preparation/publication scripts and locked tools illustrated by the existing certification fixture. Its declared Step outputs and environment imports are explicit dataflow; native failure remains authoritative.

Demonstrates explicit inputs, step outputs, env/variable derivation, and per-step imports without ambient shell state.

This package intentionally favors readability over a complete production policy. Use it to learn the Plan/Goon authoring model; do not copy it into production without adding the domain-specific admission, resource, secret, network, evidence, and release controls required by that workload.

Machine-readable truth is [`goon.yml`](./goon.yml).
