# Testing Oh-Goon contracts

Oh-Goon keeps tests that protect observable behavior, authority boundaries and reproducible defects. Test count, source coverage and an exact prose snapshot do not establish product correctness.

Reusable test-review methods live in agentsCookbook: [coverage design review](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/coverage-design-review/SKILL.md) and [test contract coupling review](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/test-contract-coupling-review/SKILL.md). The repository's mandatory owner and change-impact rules remain in [AGENTS.md](../../AGENTS.md).

## Product contracts to preserve

| Boundary | Meaningful proof |
| --- | --- |
| Plan/Goon compilation | One source graph and typed input owner; exact occurrence/output binding; invalid declarations rejected before work. |
| Fleet execution | The admitted source and selected effects reach the original Step owner; failure, cancellation and timeouts retain their real outcomes. |
| SQLite and Journal | Lease checks and owned writes share the required transaction; crash/reopen and ambiguous commit preserve one durable result. |
| Agent Skills | Frozen source and requested effects remain bound; model text grants no authority; a successful review does not make a failed test pass. |
| Public API and frontend | Generated contracts reflect backend definitions; the UI renders original backend facts and invalidates stale observations. |
| Native tools and isolation | Exercise the actual tool, process or backend when that boundary is the defect. A fixture cannot certify native behavior. |

## Local test rules

- Put tests with their owning implementation. Reserve root integration tests for behavior crossing top-level owners.
- Prefer the narrowest reliable proof of a realistic defect. Preserve distinct cancellation, source-identity, authority, persistence and recovery cases when they observe different failures.
- Keep real state isolated: database, process, filesystem, environment and admitted identities must not leak between cases. See [test setup isolation](TEST_SETUP_ISOLATION_QA.md).
- Private helper names, current filenames, exact documentation wording and test-the-test assertions are not contracts. A static check is justified when package ownership, a public protocol or an important absence property is itself the governed boundary.
- Native pytest collection owns test discovery and import identity. Do not substitute a global basename rule for the native collector; operational path/lock/protocol identities remain behavior contracts.
- Removing an obsolete or duplicate assertion is valid when stronger proof still protects the behavior. Do not replace a deleted prose assertion with new wording assertions.
- Every mutation test starts from a valid baseline and proves its specific defect is rejected. A pre-existing unrelated failure is not negative coverage.
- Use small real source/archive fixtures for policy and transition tests. Keep one composition proof for a production corpus; do not copy and reparse the whole checkout for every local mutation.
- `slow` identifies an exhaustive corpus variant with a bounded ordinary counterpart. Unique process, cancellation, permission, identity and concurrency regressions stay in routine CI even when slow. Raw timing, test count and line coverage do not decide this classification. `make release-check` restores all portable exhaustive variants.
- A skip, timeout, unavailable dependency or interrupted native/browser run remains unverified. Retain the observed failure and explain its tested source and environment; later isolated success does not erase a combined-run failure.
- Tests exercise code; coverage measures which code ran; native qualification observes supported hosts; certification belongs to the product's explicit certification owner. Cookbook review results do not substitute for any of these outcomes.

Use [Real lifecycle QA](REAL_LIFECYCLE_QA.md) for Oh-Goon's critical process, persistence, release-artifact and restart journeys. Use [Development](../DEVELOPMENT.md) for the current source commands and [Generated files](GENERATED_FILES.md) when a backend contract changes.

## Python coverage ownership

`make coverage-python` reports coverage for the Python package using both
`backend/tests` and `scripts/tests`, the explicit backend pytest configuration,
and native subprocess coverage. Privileged/model certification probes remain
explicitly selected. Repository tooling belongs in the measurement because it
is part of the same Python package.

`backend-check` runs lint, formatting, configured package type checks and ordinary
behavior tests. Coverage
percentages remain diagnostics, as required by `AGENTS.md`; they do not decide
repository correctness or APP certification. `make quality-check` explicitly
compares a complete coverage measurement to the existing committed floors.
`quality-raise` reuses that same measurement before authoring floors. The floors
are retained and cannot be lowered by the authoring command.

`make test-sqlite-coverage` and `make test-journal-coverage` are focused behavior
and resource-hygiene checks with branch-coverage diagnostics. They reuse existing
backup, transaction/replay, campaign and pinned-read regressions alongside the
original owner tests. Test failures, resource warnings and unraisable cleanup
exceptions fail these gates; a percentage alone does not. Explicit zero coverage
thresholds prevent inheritance of the generic coverage floor. The complete
`quality-check` ratchet remains unchanged.

Each focused gate retains its native data and JSON report under
`$XDG_STATE_HOME/oh-goon/coverage` (or `~/.local/state/oh-goon/coverage`). SQLite
uses `sqlite-core.coverage` and `sqlite-core.json`; Journal uses
`journal-durability.coverage` and `journal-durability.json`. They do not replace
the full-package `backend/.coverage` read by `make coverage-report`.
