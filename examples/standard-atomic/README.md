# Standard Linux/WSL Atomic Goon — actual subprocess example

This Goon explicitly uses the **maintenance** purpose for operator-owned trusted
code. It is not a CI/CD isolation sandbox and is not suitable for hostile scripts.

The original `goon.lock` was created using:

```bash
uv run --locked --project backend oh-goon lock examples/standard-atomic/goon.yml
```

The locked Goon is tested with real Linux subprocesses through the production
resolver and direct Step executor, without a mocked `run_command`:

```bash
cd backend
uv run --locked pytest -q tests/integration/test_exec01_standard_atomic_linux.py
```

This tests real success, nonzero exit, cancellation, backend-provided command
timeout, and Goon definition drift. It uses a disposable operator-owned copy
of this directory. No privileged `fs-verity`, immutable-directory ioctl,
container daemon, cgroup writer or high-security host certification is required.

**Current limitation:** This is a physical Step-run integration slice, *not*
a complete `oh-goon plan run --wait` / Fleet admission and durable Game Tape
E2E. That product-level capability remains EXEC-01B (P0). There is no Python
project in this shell-only fixture, so a `uv.lock` is not applicable here.
