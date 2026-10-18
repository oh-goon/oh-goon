# oh-goon/local-runtime

Runs the canonical local-runtime APP verification path against the already initialized
checkout. It starts the real backend and Vite frontend, proves local-access authentication
and browser-facing proxy health, then stops both processes and proves the loopback ports are
released.

This Goon runs under `execution.purpose: verification`. It does **not** acquire, inherit,
materialize, validate, or require strict Fleet executor inventory, cgroup-v2 delegation, or
systemd `Delegate=yes` authority. Production-shaped strict Fleet execution, physical Step
cancellation, crash/restart recovery, and strict parallel Shared Plan behavior remain owned by
`make strict-local-runtime-acceptance`, `make test-real-world`, and
`make test-real-world-parallel`.

This is an Oh-Goon repository verification capability. The Goon owns orchestration/evidence
only and does not redefine either the local-launch owner or strict release/CI execution.
