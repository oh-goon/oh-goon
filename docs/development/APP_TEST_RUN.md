# Development test run

## Purpose

`make test` is the fast development feedback boundary. It answers one question:

> Did this source change break code that can be tested on the current machine?

It is deliberately **not** APP self-certification. Strict executor, systemd/cgroup,
browser installation, formatting, lint, coverage floors, release packaging and
persistent certification evidence are separate boundaries.

## Run

From the repository root:

```bash
make test
```

The runner prepares backend and frontend dependency graphs independently. A setup
failure blocks only the checks that depend on that ecosystem; it does not prevent
independent checks from running.

After setup, ordinary check failures are collected rather than used as global
fail-fast gates. For example, a backend unit failure does not prevent tooling,
integration or frontend checks from running, and a frontend typecheck failure does
not prevent Vitest or the production build from running.

The current development groups are:

- dependency-light backend diagnostics first, using only already-present host packages and explicitly excluding Hashmarks-dependent tests;
- dependency-complete backend tests run directly against the native `backend/.venv`; each semantic check reports its own dependency/executable failure without a separate readiness oracle;
- repository tooling tests;
- architecture ownership checks;
- generated-contract freshness;
- backend integration tests;
- frontend TypeScript typecheck;
- frontend Vitest unit/component tests;
- frontend architecture checks;
- frontend i18n parity;
- frontend production build.

Formatting, lint, coverage/quality floors, Playwright browser certification,
backend P1 repository acceptance, local runtime acceptance and strict release/CI executor
qualification are intentionally absent from this fast path.

## Result semantics

The summary uses four states:

- `PASS` — the check ran and passed;
- `FAIL` — the check ran and found a code/product problem; independent checks continue;
- `BLOCKED` — a prerequisite for one ecosystem could not be established;
- `SKIPPED` — a check could not run because its declared prerequisite was blocked.

`make test` exits non-zero when any `FAIL` or `BLOCKED` occurred, but only after all
independent runnable checks have had a chance to execute.

This means a formatting or lint issue can never prevent code tests from discovering
real failures. Likewise, missing systemd user delegation can never prevent Python,
Node or frontend development tests from running.

## Persistent run log

Every `make test` run is streamed to the terminal and persisted outside repository
source identity under
`${XDG_STATE_HOME:-~/.local/state}/oh-goon/run-logs/<repository-key>/test/`. The
retained timestamped log is immutable for that run and `latest.log` follows the most
recent invocation. Review it without knowing the path:

```bash
make test-log
make logs
```

Logging is observational only: inability to create the log directory never blocks
tests that can otherwise execute. Run logs are operational state and never enter
source snapshots, blind exam checkouts, or source packages.

## Later boundaries

Use the more expensive/specialized boundaries for their actual purpose:

```bash
make browser-test
make check
make certify
```

- `make browser-test` proves browser behavior and requires its browser environment.
- `make check` is the fast development feedback boundary: dependency readiness, bounded backend/tooling checks, architecture/generated contracts, frontend typecheck, and frontend unit tests. It intentionally does **not** own the promotion coverage ratchet.
- `make quality-check` owns fresh Python branch coverage plus the Python quality ratchet; `make check-backend` is the backend-local Ruff + coverage + ratchet diagnostic.
- `make init` is the single normal dependency-materialization boundary: native uv/npm establish the project environments and own any registry/network use. Browser engines remain an explicit browser/release concern rather than an APP-init supply.
- `make certify` performs the evidence-bearing Oh-Goon APP verification Plan through the real
  certification frontdoor against the already-initialized checkout. It must not rebuild dependency
  environments merely to obtain strict CI/CD isolation; strict executor qualification remains a separate execution-system concern.

A successful `make test` is development evidence, not `APP PASS`.

### Backend dependency staging

The development runner intentionally separates *testability* from *dependency completeness*. Normal `make test` uses the already-materialized **native `backend/.venv`** (and npm-backed frontend): it does **not** invoke or probe the system-Python hosted diagnostic after a successful native sync. In its summary, `Host-only diagnostic SKIPPED — not requested` is a truthful scope statement, **not** evidence that the native project lacks packages. A missing `pytest`, incompatible `cryptography`, or missing `fastapi` under `/usr/bin/python3` says nothing about the dependency-complete uv environment.

`test-python-core-diagnostic` remains the explicit, separate hosted-development fallback: `make test-python-core-diagnostic` runs deterministic samples of backend unit, backend integration, and repository-tooling surfaces using compatible packages already present in the host. `OH_GOON_RUN_HOSTED_DIAGNOSTIC=1 make test` requests that separate host sampling **in addition to** the normal native gates. `OH_GOON_CONSTRAINED_HOST=1 make test` requests it for constrained-host diagnosis, along with any already-ready native owners. When explicitly requested, an unavailable host interpreter or dependency capability is reported **BLOCKED**, not silently skipped or reclassified as a native dependency failure; the direct hosted target retains its own nonzero exit. Normal native checks never acquire dependencies on behalf of the hosted diagnostic. Tests marked `dependency_complete`—including Hashmarks-dependent production/runtime tests—are excluded from the hosted sample. The diagnostic never installs, resolves, privately materializes Hashmarks, writes a lock, or mutates project dependency state.

Before collection, the hosted diagnostic now binds a machine-readable host-capability receipt to `backend/pyproject.toml`, the exact host interpreter, `project.requires-python`, and observed dependency versions. `cryptography` is a mandatory hosted capability: the observed version must satisfy the repository-owned minimum and the probe must exercise the product-facing Ed25519 sign/verify, PEM serialization/load, and X.509 certificate build/load surfaces, so stale distribution metadata cannot hide a broken native/OpenSSL binding. The capability probe runs isolated from ambient `PYTHONPATH`; the subsequent diagnostic replaces rather than extends ambient `PYTHONPATH`, preventing tests from importing a different cryptography copy than the one that was certified. An installed but incompatible/broken declared dependency blocks the diagnostic; missing non-required leaf packages remain explicit capability gaps rather than product failures. A native uv artifact-cache miss is a different fact from host-package availability and must never be described as “cryptography is unavailable.” The host capability receipt is diagnostic-only and can never satisfy native `.venv`, BP2, or certification authority. Its schema identity is owned by `repository_tooling.hosted_dependency_capability.SCHEMA`; the hosted diagnostic receipt verifier consumes that producer-owned identity rather than maintaining a second schema constant.

The certification-bootstrap stdout framing is also single-owned. `repository_tooling.certification_bootstrap_protocol` owns only the success/error record identities plus their JSON record framing. `cli_developer.py` constructs the success/error payloads and emits them through that protocol owner; `workflow.py` extracts the matching records through the same owner before applying workflow validation and decisions. Error classification, bootstrap authority, payload contents, and workflow policy remain outside the protocol module. Missing, malformed, or duplicate bootstrap records fail closed rather than allowing a copied private spelling to become a second transport authority.

`DIAGNOSTIC_SURFACE`, `DIAGNOSTIC_BATCH`, and `DIAGNOSTIC_BATCH_LIMIT` can widen the same runner without introducing another setup; for example `make DIAGNOSTIC_SURFACE=backend-unit DIAGNOSTIC_BATCH=5 DIAGNOSTIC_BATCH_LIMIT=4 test-python-core-diagnostic`.

`test-python-dependency-complete` runs the small `backend/tests/dependency_complete` surface that exercises real leaf-client compatibility. The inner `test-python` owner runs the bounded fast repair surface plus dependency-complete adapters. Final APP certification uses the distinct `test-python-complete` owner, which runs the complete backend unit surface plus dependency-complete adapters under hard batch and whole-suite timeouts. The split changes feedback timing without allowing a fast repair PASS to become certification evidence.
