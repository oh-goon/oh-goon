# EXEC-01B2A — Native standard-local qualification without substitutes

## Decision

A dedicated Linux/WSL verification command uses the **same original** FleetRun,
Enforcer, Step supervisor, SQLite journal, Game Tape and Hashmarks adapter.
The exact dependency remains owned by `backend/pyproject.toml` and `backend/uv.lock`.
No alternative repository identity implementation or alternative execution lifecycle.

`bash scripts/qualify_standard_local.sh` first performs `uv sync --locked --project backend
--group dev`, then runs the real native pytest ring with an environment flag that
**fails** if Hashmarks is missing instead of treating a skipped test as a pass.
Tests create disposable Git repositories from the locked Atomic Goon example,
register the repository with the original ApplicationRuntime, and check actual
success, nonzero failure, cancellation, CLI JSON, journal/Game Tape, and restart
readback. The test suite does not mock the external identity producer, the worker,
the process supervisor, or the persistence layer.

This command performs host **functional qualification**, not privileged host
security certification. It does not need `fs-verity`, kernel custody attestations,
privileged unsealing, new signers or special host approval.

## Current hosted limitation

This hosted runtime has no Hashmarks 0.25.1 wheel and no DNS access to the exact
GitHub release URL. Therefore its native E2E proof is `NOT_RUN/DEPENDENCY_BLOCKED`,
not `PASS`. Other regression groups may pass, and native tests may skip in the
normal development ring, but skipped native tests are not qualification.
Actual WSL is not present and is `NOT_RUN`.

## Next

Qualify with the pinned dependency on actual Linux and WSL, fix any functional
failures with focused regressions, then continue to Agent Skills through the same
canonical execution path. No high-security roadmap drift.
