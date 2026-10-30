# oh-goon 🧹

> Old hacky code, gone. If it doesn't validate, it doesn't run.

Oh Goon is a general-purpose workflow and Agent execution engine for repository maintenance, CI/CD, and explicitly bounded agentic work. It is built for stability engineers who need to run the same declared work across one repository or a fleet and inspect what happened.

**Development repository.** This source is available for development and review;
it is not a released or fully qualified product. Start a source checkout with
`make backend-install` for CLI use, or `make init` followed by `make dev` for the
browser. Ordinary Linux/WSL development does not require historical checkpoint
archives or release certification.

**Execution first. Security proportional to the workload.** Standard local work
uses the existing execution lifecycle on Linux/WSL, with process supervision,
cancellation, cleanup, and recorded outcomes. Stronger isolation applies when
the workload explicitly selects it; optional high-security qualification is
separate from ordinary development.

Execution is deliberately linear. Oh Goon does not turn Plans into DAGs, infer missing intent, merge repository pipelines into Shared Runs, or treat an earlier preview as permission to execute.

## Test this checkout

For ordinary development feedback from a fresh checkout:

```bash
make init
make test
```

`make init` is the idempotent first-checkout and reconciliation boundary. `backend/pyproject.toml` owns dependency intent and committed `backend/uv.lock` is the sole exact Python resolution authority. Native `uv sync --locked` and native `npm ci` materialize the environment and own their own network, cache, retry, installation, and environment semantics. Oh-Goon does not require a separate certification dependency universe or maintain a parallel dependency cache. `make sync` is the explicit authoring boundary allowed to update native lock bytes after intentional dependency-intent changes. `make certify` consumes that already-materialized native authority and then proves only release-critical Oh-Goon semantics; it does not invoke `make init` again.

Use the [focused edit loop](docs/DEVELOPMENT.md#focused-edit-loop) for a selected
test while editing. `make test` aggregates the ordinary backend, integration and
frontend checks for a stable batch. Independent checks continue after an
ordinary test failure. Supported Make checks verify their native CPU budget
before work; browser, quality and release checks retain their separate owners.

On WSL, dependency and test startup is usually much faster when the checkout lives on the native Linux filesystem (for example `~/oh-goon`) instead of a Windows-mounted `/mnt/c/...` path. `make test` prints a non-blocking note when it detects the slower mounted layout; the mounted layout remains supported.

Both `make test` and `make certify` retain streamed output outside source identity under
`${XDG_STATE_HOME:-~/.local/state}/oh-goon/run-logs/<repository-key>/`; the logging
redactor is unbuffered, and long certification waits emit line-oriented heartbeats plus
transient observed Goon/Step activity while durable terminal truth remains owned by the
Run projection. Use `make test-log`, `make certify-log`, or `make logs` to review it later.

For explicit APP certification, follow the [APP runbook](docs/development/APP_TEST_RUN.md)
after initialization. It is a separate workflow from starting or testing this
development checkout.


## Start

### CLI first — no frontend setup

From the repository root in a Linux/WSL shell with Git, Bash, GNU Make and
uv `>=0.10.0`:

```bash
make backend-install
uv run --offline --frozen --no-sync --project backend oh-goon --help
```

`backend-install` consumes the committed native uv lock and prepares the source
CLI, including Hashmarks. Native uv selects Python `>=3.12,<3.15`. This path does
not need Node, npm or a frontend build. Replace `--help` with a CLI command below;
the [Quickstart](docs/QUICKSTART.md#select-one-workflow-configuration) shows the
short path from repository selection to a completed local workflow.

### Browser start

To develop or use the source UI:

```bash
make init
make dev
```

Open the exact `http://127.0.0.1:5173/#access=...` URL printed by `make dev`. The source frontend runs on Vite and proxies `/api` to the backend on `127.0.0.1:8787`; the fragment is exchanged once for an HttpOnly local-access cookie and removed from browser history. `make diagnostic-system-check` is the read-only diagnostic command when startup prerequisites or lock validation need inspection; it is not an extra required startup step.

Init and verification frontdoors report elapsed time per phase and suppress recursive Make directory banners, so normal progress does not repeat the operator's absolute checkout path.

Use a Linux or WSL shell with Git, Bash, GNU Make, uv `>=0.10.0`, Node 22.16.0+ and npm 10.9.2+. `backend/pyproject.toml` owns Python `>=3.12,<3.15`; native uv selects Python and owns local resolution/environment creation. A fresh or existing checkout runs the same `make init`: native uv/npm decide whether their own caches or the network are needed. If a native operation fails, its result remains authoritative; `make init-doctor` is an explicit diagnostic-only create/write/fsync/delete path probe and never substitutes for uv/npm. Private index configuration stays local; the committed native lockfiles remain part of public source identity.

**Hashmarks is required.** It is a normal backend dependency in
[`backend/pyproject.toml`](backend/pyproject.toml), with its exact released wheel
and digest recorded in [`backend/uv.lock`](backend/uv.lock). `make backend-install`
and `make init` install it through native uv along with the other dependencies.
Repository identity and repository evidence use the [single Hashmarks integration](docs/architecture/hashmarks-oh-goon-authority-boundary.md).
A missing wheel or failed download must be resolved through the native
dependency installation; it must not be replaced with an Oh-Goon identity
implementation or a diagnostic test substitute.

Re-running `make init` is the repeatable reconciliation path. It consumes committed `backend/uv.lock` and `frontend/package-lock.json` without authoring dependency history. `backend/.venv` and `frontend/node_modules` remain generated local state, while `backend/uv.lock` is committed, packaged, and identity-bearing. The backend dependency contract is exactly `backend/pyproject.toml` (intent) plus `backend/uv.lock` (resolved authority). There is no separate developer acquisition/setup command.

When Python dependency intent was intentionally changed, `make sync` is the authoring loop: native uv resolves the new graph and updates committed `backend/uv.lock` for review; npm separately owns its committed frontend lock. `make certify` does not author dependency history or rerun dependency materialization. `make init` materializes both developer environments; `make backend-install` selects the same native uv operation for CLI-only use. Certification consumes the resulting uv/npm-owned environments. Private registry identity remains operator/native-tool configuration, never repository or certification identity.

Frontend development materialization is npm-owned. `make init` and `make frontend-install` invoke native `npm ci --prefer-offline --ignore-scripts --no-audit --no-fund`; Oh-Goon does not maintain a second `node_modules` cache, cache identity, symlink publisher, graph validator, or GC policy. Browser binaries are intentionally separate from developer initialization: use `make browser-setup` when browser diagnostics or release acceptance require them. The lean APP certification Plan does not replay the full browser matrix; `make verify` owns that broader diagnostic surface. `make browser-preflight` launches the configured engines and reports missing host libraries. Only `make browser-system-deps` asks Playwright to install operating-system dependencies and may require sudo/root. A release host without browser engines can run the same full gate in the exact lock-derived Playwright image with `make acceptance-browser-container`.

### Built release

`oh-goon app start` is for built releases only. An installed release contains the built React UI and does not require Node/npm at runtime:

```bash
oh-goon app start
```

Open the exact `http://127.0.0.1:8787/#access=...` URL printed by `oh-goon app start`. If you are working from a source checkout, use `make dev` instead.

Global installation is optional. The source `make cli-install` target builds a
standard wheel and therefore needs
the release owner's built UI first. Use `make backend-install` for source CLI
setup.

Start from the short [documentation entry path](docs/README.md); it links to [Quickstart](docs/QUICKSTART.md), [Troubleshooting](docs/TROUBLESHOOTING.md), and the owner-specific references without mirroring their policy.

## Two explicit execution modes

| Mode | Root source | Repository scope | Repository `goon.yml` |
| --- | --- | --- | --- |
| Repository CI/CD | Exact `<repository>/goon.yml` | Exactly that repository | Required and executed |
| Shared Run | One selected saved `plan.yml` | Explicit repositories | Not read, merged, validated, or used as fallback |

Both modes resolve into the same immutable execution representation and then use the same admission, executor, Journal, recovery, and Game Tape boundaries.

Choose folders in the UI to discover Git repositories. Every discovered Git root remains visible as a repository; missing or invalid `goon.yml` files are reported rather than silently skipped.

For a trusted local Python project, initialize a workflow and run it:

```bash
oh-goon init --template python/quality --repository /absolute/path/to/project --no-input
oh-goon run ./workflow --no-input
```

The project supplies its native `pyproject.toml`, `uv.lock` and declared tools.
Initialization validates caller choices, explicitly registers that exact Git root
when needed, and publishes `workflow/run.yml` with its first canonical `goon.lock`.
It reports registration and publication separately if a later operation fails.
An existing destination is refused. `--kind plan` is the default; select
`--kind goon` for a single catalog Goon.

`run.yml` uses `version: 2` and `target.repository`, a path relative to the
selection file's directory or an explicit absolute path. Repeated `--repository`
selects one Plan over an exact set in `target.repositories`; Repository Goons
and Agent/repair workflows remain singular. Existing registered
filesystem subjects remain usable. Target selection never searches parent Git
roots or registers a container's descendants. After editing source or inputs,
review the change and run `oh-goon workflow prepare ./workflow` before running.
Run performs fresh Fleet admission and waits for the recorded terminal outcome.

`templates list`, `workflow inspect`, `workflow validate` and stateless `doctor`
provide optional discovery and diagnostics. Inspection and validation are local
and read-only by default; `--acquire` explicitly permits source acquisition.
Neither diagnostic proves native prerequisites or grants execution authority.
Use `init --draft` for incomplete Agent/repair authoring; it reports NOT_READY
and requires completion and explicit preparation. The
[configuration guide](docs/development/TEMPLATE_INPUT_INSPECTION.md) owns the full
source, input, target and effect contract.

The common commands are `init`, `workflow`, `run`, `runs` and `doctor`. Built app
lifecycle commands live under `app`. Superseded `config`, `lock`, `templates init`,
Plan readback aliases and v1 selection files are refused. Historical Run and
recovery facts remain readable through their original owners.

## Plan → Goon → Step

`run.yml` selects a workflow and target; executable authoring stays in one model:

```text
Plan
└── ordered Goons
    ├── Composite Goon → ordered Goons
    └── Atomic Goon → ordered Steps
```

- A Plan selects Goons and never owns executable Steps.
- A Goon contains Goons or Steps, never both.
- Ordering and duplicates are preserved.
- Inputs cross a Goon boundary through explicit `with:` bindings or Composite
  Goon `pass:` shorthand, compiled into those same bindings.
- Requirements stay owned and provenanced by the Goon that declares them.
- The source graph is read and resolved once before admission.
- Execution remains a single ordered sequence; visualization never creates scheduler edges.

Plan, Composite Goon and tool-invoking Atomic Goon packages contain YAML and
README. An Atomic Goon with `runtime: python` instead owns a complete standalone
Python project beside its YAML:

```text
templates/goons/<category>/<name>/
├── goon.yml
└── README.md

templates/plans/<category>/<name>/
├── plan.yml
└── README.md

templates/goons/<category>/<python-name>/
├── goon.yml
├── README.md
├── pyproject.toml
├── uv.lock
├── src/
└── tests/
```

`goon.lock` is a compact deterministic fingerprint of the resolved execution structure. It does not duplicate dependency resolution:

- `goon.lock` freezes resolved Plan/Goon/Step structure and relevant external-artifact digests.
- `backend/pyproject.toml` owns Python dependency intent; committed `backend/uv.lock` is native uv's exact resolution authority.
- Each Python Goon owns its own `pyproject.toml` and native `uv.lock`; execution installs base dependencies unless its Goon explicitly selects a group.
- `frontend/package-lock.json` owns Node dependencies.
- Other ecosystem locks remain authoritative for their ecosystems.

See [Fleet Plans](docs/development/FLEET_PLANS.md), [Shared Plans](docs/development/SHARED_PLANS.md), and [CI/CD contract](docs/CI_CD_CONTRACT.md).

## Agent Skills and bounded local model work

Agent Steps use the same Resolver, Fleet execution, process supervision, and
Game Tape as other Steps. They support captured repository and Skill resources,
provider-backed review and proposals, and installed native coding agents.
The [bounded review–repair loop](templates/plans/agents/agentic-review-repair-loop/README.md)
runs ordered reviews, adjudication, a read-only proposal, native scoped apply,
regression checks and fresh review before final verification. Each phase retains
its own linked Run, candidate identity and native outcome. Callers explicitly
select review-pass, deadline and write-root limits; convergence remains separate
from release certification.

A failed test can publish a declared bounded diagnostic to a later read-only
Agent Step. The original test remains failed, and its diagnostic does not grant
edit or retry authority. The bounded repair controller owns fresh captures and
readmission; native agents retain their own reasoning, tools and compaction.

Reusable development and review Skills come from **agentsCookbook**. See
[Agent Skills](docs/AGENT_SKILLS.md) for the pinned upstream source, a recipe for
selecting a cookbook Skill in your own repository, and the current execution
contracts. The three small local Skill packages are examples and test fixtures;
they are not a separate review catalog. A cookbook checkout is optional and is
not required to build, test, or run Oh-Goon.

## Browser investigation path

The UI starts broad and discloses detail only when needed:

```text
Overview
→ repositories / Runs behind an abnormal result
→ exact Run
→ Game Tape
→ Goon
→ Step
→ logs / outputs / evidence
```

Primary areas are Overview, Repositories, Runs, Schedules, and Scout. Dependency investigation is entered from observed evidence/repository context, and a Shared Plan execution starts as **New run** from Runs rather than becoming another top-level architecture concept. Game Tape provides Overview, Timeline, Steps (including CI/CD Flow), Logs, Outputs, and Enforcer evidence while preserving the backend's recorded order and identity.

Timelines use a fixed elapsed-time domain. Compared Runs share the same scale so the visualization cannot make unequal durations look equal. Long logs and large repository sets use bounded rendering without changing backend retention or selection meaning.

See [Game Tape](docs/development/GAME_TAPE.md), [Observability ownership](docs/OBSERVABILITY.md), [Automation model](docs/AUTOMATION_MODEL.md), and [browser quality](docs/development/BROWSER_QUALITY_MATRIX.md).

## Safety and evidence

The security boundary is runtime behavior, not repository prose:

- resolution completes before admission;
- admission occurs before effects;
- repository and authored paths fail closed on traversal, symlink, hard-link, mount, and ownership violations;
- executor placement and cancellation authority are explicit and revision-bound;
- repository effects receive durable identities before execution;
- ambiguous external outcomes are quarantined for evidence-based reconciliation rather than replayed blindly;
- Journal and Game Tape preserve durable execution truth;
- generated OpenAPI/frontend contracts are committed and checked from one backend API authority;
- secrets and raw historical logs are not promoted into durable cross-run evidence.

Operators own backup scheduling, retention, and restore procedures. The backend
provides an explicit [SQLite snapshot API](docs/architecture/ADR_DB3_SCOUT_BACKUP_SINGLE_OWNER.md);
there is no automatic restore workflow. Incompatible state is preserved and requires its matching original runtime to settle execution ownership before an explicit archived-state/fresh-generation cutover. `make local-state-reset`, or `oh-goon local-state reset --yes` from the exact built release, applies only to compatible, fully settled state; confirmation cannot erase unresolved writer evidence. Saved League-folder preferences remain separate. See [schema-mismatch recovery](docs/TROUBLESHOOTING.md#local-database-schema-mismatch); do not use a different/global binary to bypass a refusal.

See [Architecture](docs/ARCHITECTURE.md) and [path isolation](docs/development/PATH_ISOLATION_HARDENING.md).

## Scout

Scout is an optional read-only cross-run view over canonical Oh Goon evidence. It can aggregate workload, CPU, memory, frequency, and Goon/Step costs, but it never admits, schedules, retries, cancels, places, or repairs execution.

Scout is downstream of execution and exposes GET-only product routes. Missing telemetry remains unavailable rather than being converted to zero. Removing Scout must not change execution behavior.

See [The Scout](docs/development/THE_SCOUT.md).

## Repository ownership

```text
backend/           Python product, API, execution, persistence, tests
frontend/          React/TypeScript presentation and interaction
templates/         Shipped Plan and Goon product artifacts
generated/         Reproducible OpenAPI, frontend, and release outputs
scripts/           Repository generation, release, maintenance, and boundary checks
docker/            Supported deployment examples
tests/integration/ True cross-owner integration behavior
docs/              Current architecture, operations, and durable rationale
```

Read [AGENTS.md](AGENTS.md) before changing ownership. Backend and frontend add only owner-local instructions in their own `AGENTS.md` files.

## Validation lanes

Use the smallest relevant check first.

```bash
make backend-check
make frontend-check
make tooling-check
make architecture
make check-generated
make test-integration
```

`make architecture` is a small repository-only lane for cross-owner dependency direction, local-only frontend networking, exact template package grammar, production-template safety, Scout's read-only dependency boundary, release identity, and the dependency-intent/resolution ownership boundary. Runtime safety remains proved by behavioral backend and integration tests.

`make check` is a broad development feedback boundary after `make init`. It
runs backend fast tests, repository-tooling tests, architecture,
generated-contract freshness, frontend typecheck, and frontend unit tests.
`make quality-check` and the owner checks above cover the separate lint,
coverage, and quality surfaces. `make verify` is the broader diagnostic run;
`make certify` owns APP certification. A development import is not a claim
that any of these broader checks already passed.

Browser matrices, containers, scale probes, and installed-wheel journeys remain
explicit qualification/release work:

```bash
make acceptance-release
make acceptance-container
make acceptance-product
make acceptance-scale
```

See [Development](docs/DEVELOPMENT.md) and [release reproducibility](docs/development/RELEASE_REPRODUCIBILITY.md).

## Dependency and release ownership

Python dependency ownership is explicit. `backend/pyproject.toml` owns intent and committed `backend/uv.lock` owns exact resolution. `make init` delegates frozen consumption/materialization directly to native uv and npm; `make sync` is the operation allowed to create new dependency-resolution history and update native lock bytes. npm separately owns committed `frontend/package-lock.json`. Public-source privacy checks reject private registry or host-absolute identity from the committed lock.

Normal startup never authors new dependency history:

```text
make init  → native uv/npm consume committed locks, using their own caches/network as needed, then tiny Oh-Goon smoke
make dev   → start the prepared source environment
```

The canonical slim source archive contains `backend/pyproject.toml`, committed `backend/uv.lock`, the committed frontend lock, and generated contracts while excluding virtual environments, `node_modules`, package-manager caches, frontend build output, generated release staging payloads, and Windows `Zone.Identifier` transport sidecars. `release-artifacts/source-manifest.json` records the backend/frontend lock digests and that native uv/npm cache state is not transported. Plan/Goon discovery likewise ignores only known disposable transport/local-state paths; unexpected package files still fail closed.

Release-package production is owned by the `oh-goon/prepare-release` Plan. Maintainers must not create the canonical retained ZIP through a separate Make packaging path. Release version authoring happens explicitly **before APP certification**. After APP PASS, the release Plan owns only deterministic ZIP publication, SHA evidence, and content-store retention through one package/store Goon operating on the exact admitted immutable repository workspace. It must not stamp, clean, normalize, or otherwise create a different source candidate after certification. The package-store owner refuses publication unless complete architecture, generated-contract freshness, and canonical Goon static admission pass immediately before packaging; it then re-runs complete architecture from an extraction of the exact ZIP bytes before the artifact becomes retained evidence.

Release acceptance must verify the wheel, bundled UI, CLI/API health, persistence across restart, generated parity, archive integrity, committed dependency locks, and clean-extract initialization. A source checkpoint missing `backend/uv.lock` or the committed frontend lock is blocked and must not be named as a release.

## Rationale

Current behavior and design decisions belong in the owning source, tests, and
[architecture documentation](docs/ARCHITECTURE.md).

## Repository contents

The publication tree contains product source, tests, executable templates and
examples, native dependency locks, generated API contracts, and current
contributor and operating documentation. Architecture decision records explain
enduring design choices; template READMEs and small Skill fixtures belong to
their executable examples.

Historical implementation plans, checkpoint manifests, handoffs, and old status
reports are absent from the starting source archive. Keep retained history and
local build/run output outside the publication tree; `.gitignore` also prevents
known historical and generated files from being added accidentally. A fresh
checkout does not need history for ordinary development checks. Explicit
archive/seal commands consume their real metadata separately and do not
establish release readiness for this tree.

## Junior/operator certification path

Junior agents should use exactly one workflow document: [`docs/development/APP_TEST_RUN.md`](docs/development/APP_TEST_RUN.md). Do not construct a certification or release sequence from individual Make targets, acceptance reference docs, `CHANGELOG.md`, `HISTORY.md`, or checkpoint history. APP Plan ownership comes first; after APP PASS, follow [`docs/development/PACKAGE_RUN.md`](docs/development/PACKAGE_RUN.md). Package production is owned by `oh-goon/prepare-release` only.

## Portable local verification

After `make init`, use `make verify-local` for the P0 execution checks and P1
real-repository authoring checks without the browser matrix. See
[Local verification](docs/LOCAL_VERIFICATION.md) for its exact scope and the
separate release/browser workflow.

## License

OH-Goon is licensed under the [MIT License](LICENSE).
