# AGENTS.md — repository contributor contract

This file owns repository-wide instructions. `backend/AGENTS.md` and
`frontend/AGENTS.md` add only owner-local rules. Read the applicable owner map
before changing code; keep instructions here specific to Oh-Goon.

## Ownership and navigation

| Change | Owner and entry point |
| --- | --- |
| Domain, API, execution, Python package | `backend/`; read its `AGENTS.md`, `README.md`, then `src/oh_goon/README.md` |
| React/TypeScript behavior | `frontend/`; read its `AGENTS.md`, `README.md`, then `src/README.md` |
| Executable Goon and Plan packages | `templates/`; package rules below |
| Architecture, generation, release, repository checks | `scripts/` launchers; implementation in `backend/src/oh_goon/repository_tooling/`, tests in `scripts/tests/` |
| Hosted repository CI | `.github/workflows/`; invoke the existing Make checks; GitHub owns check scheduling and merge enforcement |
| Generated OpenAPI/frontend contracts | `generated/`; change the authoritative backend source or generator |
| Container/deployment examples | `docker/`; backend/release owners retain runtime semantics |
| Cross-owner integration | `tests/integration/`; backend-only integration stays under `backend/tests/integration/` |
| Current explanations and decisions | `docs/` or the relevant owner README; start at `docs/README.md` |

The top-level tree is the ownership map. Do not add root `src/`, `ux/`,
`shared/`, `common/`, `utils/`, `lib/`, `app/`, or `packages/` surfaces.
There are exactly three `AGENTS.md` files: root, backend, and frontend.
`backend/src/oh_goon/repository_tooling/architecture/repository_layout.py` owns physical-layout checks.
Extend an existing owner rule when its invariant already covers the change.

Shared Runs use `backend/src/oh_goon/services/fleet_plans.py` and existing Fleet
owners for one Plan over multiple repositories. Scheduling belongs to
`backend/src/oh_goon/services/schedules.py` and `backend/src/oh_goon/runtime/schedule_scheduler.py`: explicit repository
targets, one resolved Plan, and a wall-clock interval; each target becomes its
own Run. Do not introduce another scheduler or executable-recipe owner.

## Product authority

Ordinary Plan/Goon execution defaults to trusted local verification and waits for
completion. Deep custody and privileged native qualification apply only to explicitly
selected strict execution for untrusted code or requested additional guarantees.
Keep normal startup and provider configuration simple; see `docs/CI_CD_CONTRACT.md`.

Oh-Goon orchestrates native tools; it does not reimplement their semantics.
Native uv, npm, pytest, Ruff, mypy, Git, Docker/BuildKit, Kubernetes, and operating
system facilities retain their own resolution, cache, process, and result
authority. Do not add a parallel resolver, state machine, fallback proof, or
replacement algorithm. Required evidence that cannot be established remains
`INCOMPLETE`. See `docs/development/NATIVE_TOOL_AUTHORITY.md`.

Resolve configuration once, transport it explicitly, execute once, and report
once. The receiving process verifies the resolved authority rather than reading
ambient defaults again. Preserve caller configuration and emit startup facts
from the actual execution owner. Avoid second discovery walks, source reads,
and interpretations when an exact resolved representation already exists.

Reject an invalid state before work when its responsible owner can already
know it. Workload declarations must convey new capability information, not
repeat ordinary platform or committed repository authority. Source positions,
line counts, prose, private names, lint debt, and other quality proxies cannot
decide APP certification. Plan/Goon identity is provenance unless an explicit
public contract makes it behavioral authority. See
`docs/development/FAIL_BEFORE_WORK.md`.

Effectful lifecycle transitions validate their incoming authority before the
first effect and report convergence. `release-version-set` owns native version
authoring, regeneration, and rollback of its authoring state on failure.
`make certify` admits the exact source independently; `make prepare-release`
requires APP PASS for that source. Diagnostics such as `make verify-p0` do not
become extra lifecycle prerequisites. Deep dogfood binds to the retained
artifact digest and remains diagnostic evidence.

Preserve execution, validation, privacy, isolation, trace, and failure semantics
unless the task explicitly changes them. Current owner decisions are in
`docs/ARCHITECTURE.md` and `docs/development/ARCHITECTURE_DECISIONS.md`.
Update the owning current documentation when a contract changes; do not create
phase/status files or runtime prose gates. See
`docs/development/DOCUMENTATION_ANTI_DRIFT.md`.

## Resource and workspace ownership

Every persistent artifact needs one owner and a lifecycle: execution scratch,
bounded evidence, authoritative receipt, or explicitly promoted artifact.
Keep minimal evidence rather than treating caches and complete workspaces as
receipts. Cleanup must respect active leases, survive failure/cancellation,
and report its own failure without rewriting the product result. Storage
pressure must fail safely; age alone is not proof that an artifact is unused.

Close owned files, sockets, database handles, pipes, and process resources
deterministically. Stream or bound external payloads before accumulation;
prefer file-backed transfer and explicit last-use boundaries for large data.
Do not add blanket `del`, `gc.collect()`, or warning suppression. Exact small
immutable authority payloads may be read atomically; this is a lifetime rule,
not a ban on a particular method spelling.

Durable workspace identity and host-path resolution belong to
`runtime/workspaces.py::RepositoryWorkspaceAuthority`. `RunPathLayout` and
`AttemptPathLayout` own disposable execution scratch. A path alone is not
workspace authority. See the [identity owner map](docs/development/ARCHITECTURE_DECISIONS.md#git-is-optional-repository-native-metadata-not-source-authority)
for catalog, subject, content, workspace, admission, and physical custody identities.

## Hashmarks, coding agents, and Skills

- **Hashmarks = repository-evidence plane.** It owns repository-evidence/content
  identity and freshness, retrieval/CodeMap, impact, context and verification selection,
  exact flat verification membership, repository-negative evidence, and
  repository-evidence provenance.
- **Coding agent = solution plane.** It owns solution reasoning and editing.
  Neither Hashmarks nor Oh-Goon decides which code change to make.
- **Oh-Goon = execution and certification control plane.** It admits evidence
  and owns execution policy, runtime/process authority, scheduling, isolation,
  timeout/retry/resume/recovery, verification-result authority, execution
  provenance, Repository Understanding qualification, certificates, and Game Tape.

Consume supported Hashmarks Python APIs only through
`backend/src/oh_goon/integrations/hashmarks/`. Select its released dependency
with `backend/pyproject.toml` and native `uv.lock`; do not vendor, reacquire,
repackage, or maintain a parallel release/commit/wheel identity. Bind evidence
compatibility to the implementation identity reported by the loaded runtime.
Hashmarks is required: runtime startup must establish its loaded identity before
stateful work. Agent model requests receive producer task evidence from the
exact captured repository before reasoning, without an opt-out or local evidence
fallback. Preserve producer qualifications and provenance in prompt delivery;
bounded evidence does not reduce an admitted review scope.
New repository-intelligence capabilities belong in Hashmarks first, including
CodeMap, repository-derived relevance, decision packets, verification membership,
negative evidence and evidence economics. The coding agent owns hypotheses,
attempt memory, scout/delegation choices, model routing and solution strategy;
Oh-Goon retains execution recovery and workflow control.

Hashmarks nominations are flat evidence, not execution batches. Oh-Goon's
existing Fleet/Step owners handle grouping, concurrency, process launch,
cancellation, deadlines, retry/resume, timeout bisection, and result accounting.
Do not call Hashmarks execution-loop helpers or build an independent selector,
impact graph, scheduler, or agent harness. Successful accounting must preserve
an exact one-to-one partition of admitted membership, without omission,
duplication, or added members. A nomination without structural owner-authority
evidence remains unproven; do not upgrade it to `covers owner`.

Repository retrieval never owns grading, scoring, qualification admission, or
certification audit. SECRET answer truth remains grader-only and outside
Hashmarks repository evidence. Qualification owners consume admitted/frozen
evidence and must not import repository-intelligence semantics. Keep
repository-evidence provenance distinct from execution provenance and
verification-selection authority distinct from verification-result authority.
See `docs/development/HASHMARKS_EXECUTION_LAYER.md`.

Reusable development and review Skills are owned by **agentsCookbook**. Use
its pinned canonical Skills as optional guidance; keep Oh-Goon ownership,
contracts, and commands here. Oh-Goon must build, run, and test without a
cookbook checkout. Caller-selected Skills use the existing Step lifecycle;
Skill text grants no effects or result authority. The small local Skill files
under `examples/` are product examples and test fixtures. See
`docs/AGENT_SKILLS.md` and
`docs/development/AGENTS_COOKBOOK_STRUCTURE_DOGFOOD.md` for the external pin,
actual Skill paths, and ownership boundary. Fix general methodology defects
upstream, product defects here, and Hashmarks defects in Hashmarks.

## Generated contracts and template packages

API changes flow in one direction: backend API source → captured OpenAPI →
`generated/openapi/openapi.json` → `generated/frontend/` → authored frontend.
Do not repeat endpoint method/path/request/response facts in frontend code or
introduce a backend dependency on frontend implementation. Generation captures OpenAPI
once; downstream generators consume that artifact. Never hand edit generated
files. `make generate` writes contracts; `make check-generated` checks freshness.
`generated/release/` is release output. Frontend builds must not mutate backend source.

Each shipped package contains exactly YAML and README:
`templates/goons/<category>/<name>/{goon.yml,README.md}` or
`templates/plans/<category>/<name>/{plan.yml,README.md}`.

- Plan owns run intent and ordered `goons:`, never executable steps or commands.
  Atomic Goon owns `steps:`; Composite Goon owns `goons:`; exactly one form.
  Plans and Goons may import Goons; neither imports Plans.
- Identity is exactly `category/name`, owned by the package path. Shipped Goon
  YAML does not repeat `name:` or `description:`. No lookup aliases, fuzzy discovery,
  legacy lookup, inheritance, overlay, patching, hidden deduplication, or reordering.
- YAML owns machine facts; duplicate keys are invalid. README explains the
  package with heading `# category/name`. No sidecars or third classification level.
- Plans automatically expose unique unbound direct public inputs; optional `inputs:`
  narrows that interface. Composite Goons explicitly declare public names. Inputs
  cross boundaries through projection, `with:` or import-local `pass:`. The compiler expands
  forwarding into those bindings; multiple recipients must be explicit. Requirements
  stay provenanced to their declaring Goon. Import-local `as:` names one occurrence
  without changing its package identity or lookup. Preserve ordered duplicate edges.
- Project-owned Goons use an explicit import `from:` catalog directory confined to
  the root authoring workspace. Omitted `from:` selects the shipped catalog;
  local packages never shadow shipped imports. Shared Plan storage owns only its
  Plan file and refuses external local imports. See `templates/README.md`.
- Classification is backend-owned and category-derived: `python`, `security`,
  `oci`, `kubernetes`, and `agents` are production; `advanced`, `general`, and `ml` are
  teaching; `oh-goon` is internal. Unknown categories fail. Production imports
  production only; teaching/internal may reuse production but not each other.
- Read and parse each source once; derive compilation and source positions
  from the same syntax tree. Analyze each unique Goon once and reuse the graph
  while retaining Plan → Composite Goon → Atomic Goon → Step provenance.

## Tests and change-impact checks

Backend unit tests follow `backend/tests/unit/<owner>/`; CLI tests use
`entrypoints/`. Backend-only integration uses
`backend/tests/integration/{api,engine,services}/`. Frontend tests are colocated;
`frontend/tests/` is shared support. Tooling tests live in `scripts/tests/`.
Reserve root `tests/integration/` for actual cross-owner behavior.

Every reproducible defect fix needs focused regression proof when it adds
defect-detection power. Protect observable behavior and meaningful negative
boundaries; retain unique browser/native evidence. Tests must survive
behavior-preserving private renames. Remove obsolete prose assertions,
implementation mirrors, tests-of-tests, and duplicate coverage without losing
contract coverage. Static inspection is justified when governed structure or
an absence property is itself the contract. Test count and line coverage are
diagnostics, not quality targets. Local policy is in
`docs/development/TEST_QUALITY.md`; reusable review methods belong in agentsCookbook.

Run the narrow owner check first and diagnose its first failure. Run the union
of applicable escalation rows after the focused checks; do not regenerate
unrelated output or broaden testing to hide a failure.

| Changed boundary | Required escalation |
| --- | --- |
| Public API/OpenAPI | `make backend-check`, `make generate`, `make check-generated`, `make test-integration`, `make frontend-check` |
| API generator/schema compiler | `make test-tooling`, `make generate`, `make check-generated`, `make test-integration`, `make frontend-check` |
| Shipped Goon/Plan package or template layout | focused backend template tests, `make architecture`, `make backend-check` |
| Repository/source/test layout, AGENTS, owner maps | `make test-tooling`, `make architecture`, `make tooling-check` |
| Release assembly/version identity | `make check-generated`, `make tooling-check`, `make build-release` when dependencies are available |
| Frontend behavior | `make frontend-check` |
| Browser, accessibility, visibility, theme | `make theme-contrast`, focused browser test, `make certify` for canonical APP completion; `make browser-setup` if engines are missing |
| Backend-internal behavior | `make backend-check`; regenerate only for an API change |
| Documentation only | owning documentation checks; no regeneration unless its contract changed |

Use existing ownership and these triggers; do not add another impact manifest,
graph, quality gate, or change-classification framework. Responsibility and
naming rules are in `docs/RESPONSIBILITY_COMPLEXITY_REFACTORING.md`: retain
cohesive owners, extract real responsibilities, and use explicit domain names.
Code size alone is not a reason to split an owner or add helper layers.

## Development commands and source transport

```sh
make                  # command map; no setup side effect
make init             # consume committed uv/npm locks and smoke-test the CLI
make sync             # author new locks only after an intentional dependency change
make backend-check
make frontend-check
make tooling-check
make architecture
make generate
make check-generated
make test-integration
make check
make build-release
```

Use Python through uv and `backend/pyproject.toml`. `backend/uv.lock` is native
resolution authority; `backend/.venv` is generated local state, never source
or package identity. An offline uv cache miss does not prove a host package is
absent. `make test-python-core-diagnostic` may validate compatible host
capabilities for diagnostics only; it does not replace native setup or gates.
Do not introduce ambient `PYTHONPATH`, system-site packages, or dependency fallbacks.

Keep archives, recovery stores, patches, reports, logs, and editor/OS sidecars
outside the source checkout. Known Windows `:Zone.Identifier` and
`:Zone.Identifier:$DATA` transport sidecars are removed only by the explicit
source-layout owner: `make source-transport-clean` is its fresh-checkout
bootstrap. Physical admission/packaging must reject contamination until that
repair runs; init, validators, and packaging must not silently clean source.
`make sync` admits source before lock authoring or environment materialization.
Do not exempt arbitrary colon names or unknown files.

The existing `make qualify-development-wsl` flow owns native qualification:
prepare once, verify once, then execute isolated rings directly with the
verified environment. No in-ring setup, dependency repair, fallback, or second
front door. Ordering/authority changes require focused negative proof; see
`docs/development/WSL_QUALIFICATION_ENVIRONMENT_CONTRACT.md`.

## Optional sandbox provider contract

OpenSandbox is an execution provider, not Python dependency authority; do not
require its Python SDK merely for admission. The deny-all remote backend is
VM-only (Kata or Firecracker); current runc/gVisor restrictions remain in force.
Remote claims require attestation bound to the endpoint, runtime, and server
configuration. Provider executable bytes participate in configuration identity;
secret values stay in environment/header authority, never CLI arguments.
Apply these requirements to that selected workload; routine local development
does not gain a new remote-provider qualification prerequisite.

## Continuation and completion

Ordinary Git contributions use the selected branch/commit and reviewed changes,
then normal commits and synchronization. Historical checkpoint files and
recovery stores are not prerequisites for a GitHub clone.

For an explicitly archive-managed campaign or requested portable checkpoint,
follow `docs/development/DURABLE_CONTINUATION.md`. Resume the newest verified
parent-linked authority, not a convenient older ZIP. Keep recovery transport
outside source. Close small coherent source/authority slices with focused
proof, durable persistence, and independent full-graph readback before the next
independent mutation. Partial persistence is `PERSISTENCE-INCOMPLETE`. Read-only
checks do not require ledger-only generations. Group coherent documentation
cleanup where one proof suffices; do not make each checkpoint a full release.
Run the required full qualification/release closure once for the exact final
source when that release is requested. Unavailable gates stay `NOT_RUN`/`BLOCKED`.

Report completion in the response, not in new repository status files:

- **Changed owner:** actual source owners edited.
- **Focused validation:** exact checks and results.
- **Escalation gates:** required broader checks and results, or a boundary reason
  when not required.
- **Generated contracts:** current, regenerated + current, or not applicable.
- **Architecture:** passed when architecture/navigation/template/tooling changed,
  otherwise not required.
- **Unverified:** exact failed, skipped, unavailable, interrupted, or unrun checks.

A timeout or skipped suite is unverified. Never claim all tests pass unless all
tests ran. Keep contributor instructions concise and link owning documents;
there is no arbitrary line-count gate.

Ordinary backend/integration gates exclude the existing `certification` marker.
Privileged custody, fs-verity and installed-model proofs belong to explicitly
selected strict/native qualification. Keep their portable negative/lifecycle
checks in ordinary validation; see `backend/tests/README.md`.
