# oh-goon/local-runtime

Runs the canonical local-runtime APP verification path against the already initialized
checkout. It starts the real backend and Vite frontend, proves local-access authentication
and browser-facing proxy health, then stops both processes and proves the loopback ports are
released.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**Use:** run the repository-owned Make/certification route from the prepared checkout. [`goon.yml`](goon.yml) owns the execution contract; [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) explain captured workspace inputs.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

This Goon runs under `execution.purpose: verification`. It does **not** acquire, inherit,
materialize, validate, or require strict Fleet executor inventory, cgroup-v2 delegation, or
systemd `Delegate=yes` authority. Production-shaped strict Fleet execution, physical Step
cancellation, crash/restart recovery, and strict parallel Shared Plan behavior remain owned by
`make strict-local-runtime-acceptance`, `make test-real-world`, and
`make test-real-world-parallel`.

This is an Oh-Goon repository verification capability. The Goon owns orchestration/evidence
only and does not redefine either the local-launch owner or strict release/CI execution.
