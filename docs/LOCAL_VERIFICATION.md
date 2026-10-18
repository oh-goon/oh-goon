# Portable local verification

Use this workflow to verify an Oh Goon source checkpoint in a different local environment without recreating the environment that produced the checkpoint.

Development checkpoint handoffs use the canonical product `VERSION`: changed source bytes advance the patch component, the distributable archive is named `oh-goon-<VERSION>.zip`, and its extraction root is `oh-goon-<VERSION>/`. Backend/frontend package-manager versions are release metadata and are not rewritten merely to advance a development checkpoint; uv/npm remain their native owners. PENDING/BLOCKED/debug phase text belongs in checkpoint evidence and handoff notes, not in the ZIP filename.

The portable gate deliberately separates **source-checkpoint verification** from **canonical release certification**.

## One-command P0 + P1 verification

Requirements:

- `uv >= 0.10.0`
- Python compatible with `backend/pyproject.toml` (resolved by uv)
- Git
- a successful `make init`, which delegates locked backend materialization to native uv and frontend materialization to native npm

Run:

```bash
make verify-local
```

`make verify-local` executes P0/P1 directly through the materialized project environment and named project entrypoints. It does not run predictor-only `command -v` gates: a missing native executable or project entrypoint is reported by the operation that actually needs it. No `PYTHONPATH` shadow import surface or ambient Python command is used.

Normal Oh-Goon checkout consumers use the committed backend lock with frozen semantics after `make init`; repeated Python execution uses `uv run --offline --frozen --no-sync` so execution cannot repair or mutate the environment. Only explicit authoring paths such as `make sync` intentionally update committed Python resolution history.

## Development testing and APP certification

The repository ships `oh-goon/app-certification`, an evidence-bearing Plan that
runs the 14 canonical application gates in a fixed order. It intentionally
excludes release versioning, packaging and persistent-artifact work. For the operator/junior entrypoint, see
[`development/APP_TEST_RUN.md`](development/APP_TEST_RUN.md). Architecture and debugging
rules remain in [`development/APP_CERTIFICATION.md`](development/APP_CERTIFICATION.md).

For a fresh checkout, let the native package managers materialize their environments directly:

```bash
make init
make test
make certify
```

If native uv cannot reach public PyPI in a corporate/internal network, configure the required index with **uv itself**, not in Oh-Goon. A user-level example is:

```toml
# ~/.config/uv/uv.toml
[[index]]
url = "https://<your-internal-pypi>/simple"
default = true
```

The native environment alternative is `UV_DEFAULT_INDEX`. Keep host-specific URLs/credentials out of repository `pyproject.toml`; certification inherits uv configuration and reports only whether an override/config source is present, never the URL.

`make init` is the developer materialization frontdoor: native `uv sync --locked` and native `npm ci`
own network use, caches, retries, installation, and environment state. Oh-Goon adds only sequencing and
a tiny executable smoke. There is no separate developer acquisition/setup command;
not separate dependency authorities. A native package-manager failure is reported as that native failure;
Oh-Goon does not build a second cache, retry protocol, resolver, or fallback proof around it.

An internal runner may already import a package that the current project environment does not yet
materialize. Those facts are independent. Never rewrite a declaration such as `cryptography>=46.0.4`
into `cryptography==46.0.7` merely because 46.0.7 is the current lock selection, and never turn a
registry/cache failure into a package-unavailability claim. Use `make backend-python-capability-check`
when explicit project-environment runtime evidence is useful; it observes installed distribution identity,
importability, and application-specific runtime capability without re-deciding uv-owned version compatibility. It is diagnostic and is not part of
normal startup.

`make test` deliberately excludes browser and quality/release gates so independent code tests can surface failures with minimal environmental blockers. `make certify` performs the evidence-bearing APP verification Plan against the exact already-initialized checkout. It does not create a certification workspace dependency environment, rerun `make init`, select another interpreter, or reacquire packages. There is no certification-only package supply or offline reconstruction layer. Use `make sync` only when dependency manifests were intentionally changed and authoritative locks need reconciliation.

The evidence-bearing command is:

```bash
make certify
```

During certification repair, rerun the failed Atomic Goon's canonical command rather
than repeating already-green long-running Goons. Final `APP PASS` requires one new
uninterrupted complete `make certify` Run.

Do not start `prepare-release` until that complete clean APP Plan passes.

## P0 — execution/security closeout

Run independently after preparation:

```bash
make verify-p0
```

P0 verifies the execution boundary that must remain true after the authoring simplification:

```text
source
  ↓
resolve + validate
  ↓
resolved execution identity / goon.lock
  ↓
admission
  ↓
execution / retry
  ↓
evidence
```

The gate covers canonical input resolution, execution identity, lock integrity, admitted execution, readmission lineage, workspace isolation and the simple-template guardrail. It also verifies generated backend/frontend contracts without regenerating them.

Environment-backed input values are usable by execution but are redacted from resolution diagnostics. `goon.lock` contains digests of resolved execution identity rather than raw external values.

## P1 — real repository certification

Run independently after preparation:

```bash
make verify-p1
```

### Repository authoring certification

A temporary real Git repository is created with:

```text
pyproject.toml
VERSION
project.yml
verify_values.sh
```

A Plan resolves canonical inputs from:

```yaml
version:
  from:
  - toml:
      path: pyproject.toml
      key: project.version
  - text:
      path: VERSION

image_name:
  from:
    yaml:
      path: project.yml
      key: project.imagename

timeout:
  from:
  - env: HELM_TIMEOUT_OVERRIDE
  - env: HELM_TIMEOUT

revision:
  from:
    git: commit_short_sha
```

The same `text:` source works for a named text file when that repository uses one:

```yaml
version:
  from:
    text:
      path: version.txt
```

The certification proves that the values are validated and normalized before repository code receives them, that TOML can fall back to a one-line text source, that source changes change execution identity, and that invalid values fail before execution.

The certification executes the resolved repository command itself after resolution, so P1 proves that ordinary repository code receives the canonical validated values. Strict server-side CI/CD admission is intentionally a separate environment-backed acceptance because Oh Goon requires a configured isolated executor rather than silently falling back to host execution.

## Project computation stays in project code

Resolution and validation remain declarative. Project computation stays in ordinary code.

For example:

```toml
[project.scripts]
resolve-helm-release = "project.deploy:resolve_helm_release"
```

```python
def resolve_helm_release() -> None:
    # Read the already-normalized execution inputs and implement project policy here.
    ...
```

```yaml
steps:
- run: uv run resolve-helm-release
```

Do not move `if`, `case`, concatenation or shell helper libraries into Goon YAML.

## What happens after source verification

`verify-local`, `verify-p0`, and `verify-p1` are diagnostic/source-verification capabilities. They are not an alternate certification sequence and they do not authorize packaging.

For junior/operator work, return to the canonical APP workflow in [`development/APP_TEST_RUN.md`](development/APP_TEST_RUN.md). A release/package run may start only after one uninterrupted complete `oh-goon/app-certification` Plan Run has produced APP PASS. Package production is then owned only by `oh-goon/prepare-release`. Persistent-artifact certification consumes the exact retained ZIP and runs the same APP Plan against its clean extraction.

Do not assemble a release workflow from the individual `release-*`, browser, acceptance, or packaging Make targets documented elsewhere. Those targets are implementation or diagnostic capabilities owned by their Plans.

### Dependency acquisition, cache reuse, and lock convergence are separate authorities

A successful `make init` proves that native uv accepted `backend/pyproject.toml` and the committed `backend/uv.lock` as one current authority and materialized that locked environment. `make backend-lock-check` performs the same native uv freshness assertion without authoring the lock, followed by Oh-Goon publication-policy checks.

If native `uv lock --check` or `uv sync --locked` is blocked by required registry/artifact transport, the result is `INCOMPLETE / REGISTRY_CONNECTIVITY_REQUIRED`. Oh-Goon does not substitute an offline or reconstructed convergence algorithm. Restore transport and rerun the same native uv operation; a stale or unverified lock must not be sealed as release authority.

Native uv/npm caches are outside Oh-Goon authority. Oh-Goon does not maintain identity-keyed dependency generations, shared `node_modules` trees, or package-manager cache pruning as part of startup or verification.
