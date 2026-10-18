# oh-goon/app-certification

Canonical final APP verification for one exact initialized repository revision.

`make init` is the single normal dependency/interpreter materialization boundary. This Plan has `execution.purpose: verification` and consumes the initialized repository directly; it must not recreate `.venv`, rerun `uv sync`/`npm ci`, select another interpreter, or reacquire already-present packages.

The Plan is intentionally small and proves only semantic APP authority:

1. P0 execution/security and generated-contract freshness;
2. semantic repository architecture/ownership boundaries;
3. exact shipped-template catalog startability through canonical fixtures and production admission, stopping before Step work;
4. frontend functional readiness; and
5. deterministic local runtime start/probe/stop.

Fresh bootstrap/release reproducibility and broad backend/tooling/integration/P1/browser diagnostics are separate owners.
