# Quickstart

You do not need to understand SQLite, the Journal, capabilities, or operation schemas to use oh-goon.

## 1. Prepare a source checkout

For CLI workflows, start in the repository root with Git, Bash, GNU Make and
uv `>=0.10.0` in a Linux/WSL shell:

```bash
make backend-install
uv run --offline --frozen --no-sync --project backend oh-goon --help
```

Native uv consumes the committed backend lock and selects Python
`>=3.12,<3.15`. No Node, npm or frontend build is needed for the source CLI.
Use the full `uv run ... oh-goon` prefix in place of `oh-goon` in the examples
below, then continue to [Select one workflow configuration](#select-one-workflow-configuration).

For the browser, initialize both owners and start the source UI:

```bash
make init   # native uv sync + native npm ci + tiny Oh-Goon smoke
make dev
```

Open the exact `http://127.0.0.1:5173/#access=...` URL printed by `make dev`. The Vite frontend proxies `/api` to the backend on `127.0.0.1:8787`; the one-time fragment establishes the process-local session and is removed from browser history. This browser path also requires Node 22.16.0+ and npm 10.9.2+. `make diagnostic-system-check` is available when you need read-only environment and lock diagnostics, but it is not a startup prerequisite.

Hashmarks is required and installed by `make backend-install` or `make init`
from the normal backend dependency lock. No separate Hashmarks installation or agentsCookbook checkout
is needed. A native dependency failure must be resolved before running the
affected workload; diagnostics do not replace the real Hashmarks integration.

A new operational database is created automatically. If an existing database is incompatible, preserve it and follow [schema-mismatch recovery](TROUBLESHOOTING.md#local-database-schema-mismatch). Active or unresolved execution ownership must be settled before a fresh state generation; reset cannot bypass that boundary.

### Verify a source checkout

For ordinary development feedback:

```bash
make init   # fresh or existing checkout
make test
```

Use `make check` for the repository's fast development check sequence. When you
explicitly need APP certification, follow the
[APP runbook](development/APP_TEST_RUN.md) after initialization. Browser setup,
strict executor preparation, and high-security host qualification are separate
from starting a normal source checkout.

`make init` delegates dependency materialization directly to native uv and npm. uv consumes committed
`backend/uv.lock` with frozen semantics and owns its environment, cache, registry, retry, and installation
behavior; npm owns `package-lock.json`, `node_modules`, cache, and install behavior. Oh-Goon does not
require a separate acquisition phase, does not maintain a second dependency cache, and does not infer
dependency availability from cache or registry failures. There is no separate developer acquisition/setup command.
Certification consumes the already initialized native environments. Use `make sync` only after intentionally changing
dependency intent; review the native-authored lock changes before committing.

## 2. Built release startup

`oh-goon app start` is for built releases only. A built release already contains the React UI, so Node/npm are not runtime dependencies:

```bash
oh-goon app start
```

Open the exact `http://127.0.0.1:8787/#access=...` URL printed by `oh-goon app start`. Oh Goon binds only to loopback and also requires the process-local access capability; loopback alone is not treated as authorization. No cloud account or remote control plane is required.

If you are in a source checkout and see `The bundled oh-goon UI is not present`, use `make dev` instead.

## 3. Add a League folder

On **Repositories**, choose the parent folder containing your repositories. That saved root is a **League folder** and remains saved on this computer between starts. oh-goon scans beneath it for Git roots and shows every discovered repository as a **Repository**.

The canonical repository CI/CD entrypoint is exactly `<git-root>/goon.yml`. A repository without it remains visible as **Missing**; an unsafe/unstable entrypoint remains visible as **Invalid**. Scanning is read-only and never executes the file.

## 4. Run a repository locally

For a repository marked ready, press **Run CI/CD** in Repositories. oh-goon resolves that repository's current root `goon.yml`, runs the normal Enforcer admission, then executes exactly that repository through the canonical local FleetRun lifecycle.

The browser run waits for the same canonical lifecycle and shows failure/cancellation in Runs and Game Tape. A Git repository does not need `pyproject.toml` merely to be a CI/CD target; language-specific files are requirements declared by its own `goon.yml`. CLI equivalents are collected in the **CLI workflows** section at the end.

### First local launch

If no League folder is configured, the Repositories page starts with one folder picker. Choose the **parent folder that contains your local Git repository folders**. Adding it only saves that path and scans beneath it for Git repository roots; incidental ancestor folder names do not hide repositories. It does not execute Goons or modify repositories. The folder browser stays visible while loading so Add folder never appears to do nothing.

After discovery, use **Run CI/CD** for that repository's own `goon.yml`, or open **Runs → New run** for Shared Plan execution. The browser projects the backend flow directly: **Plan → repositories → backend verification → Run → Game Tape**. No wizard/readiness state machine is stored in the browser.

### Apply one Shared Plan to several repositories

Use **Runs → New run** when the same selected Plan should run across several local repositories. The everyday flow is intentionally short:

1. **Plan** — select the saved Shared Plan you want to apply.
2. **Repositories** — select explicit repositories. For a large fleet, search by repository name/path, use **Selected only**, and use **Select visible / Clear visible** for the filtered set. Filtering never changes the selected run scope.
3. **Backend verification** — this happens automatically whenever Plan or repository selection changes. The browser shows the returned ready/blocked counts and exact backend reasons; it does not calculate readiness itself.
4. **Run** — remains disabled until the latest backend verification says the entire exact selection can run. The browser never silently drops blocked repositories or converts the request into partial execution.
5. **Game Tape** — review **Overview → Timeline → Logs** first, then open deeper Flow/evidence/analysis views when needed.

If a run fails, start in Game Tape. It remains read-only and shows the canonical safe next action. Ordinary failures stay in Runs, where you can rerun the exact recorded target after you fix the repository; `EXECUTOR_PLACEMENT_LOST` uses **Retry placement**, which creates a backend readmission linked to the failed run; uncertain or partially committed repository effects send you to **Recovery** and never expose blind replay.

The Shared Plan `plan.yml` lives in oh-goon's local configuration and is not copied into repositories. For this run each repository's own `goon.yml` is completely omitted: it is not read, validated, merged, inherited, or executed. Exact Shared Plan source/revision history and preview details remain available without changing the normal run path.

The same operations are available through the CLI; see **CLI workflows** below.

Preview is read-only and uses the same declared Enforcer/admission requirements as the run. Run remains a separate explicit action. The selected repositories only need to be current managed Git repositories; their repository `goon.yml` may be configured, missing, or invalid because it is not a source for the Shared Run.


### Create and maintain a saved Plan

Open **Plans** from the standard navigation. Plans is the single browser authoring surface for saved Plans: create a Plan, start from a shipped template, or edit its `plan.yml`. A Plan still only selects and orders Goons; executable Steps remain owned by Atomic Goons.

**Runs → New run** never edits or creates Plan source. It selects one saved Plan, renders its explicit inputs, selects repositories, requests backend verification, and then starts the exact accepted Run. If no saved Plan exists, open Plans first.

Before Run is enabled, the browser automatically requests the read-only backend preview. A ready result means the Plan resolves for every selected repository. A blocked result exposes the backend admission/resolution reason rather than reconstructing policy in the browser.

## 5. Use the everyday browser drill-down

- **Overview** — start broad, then drill into the exact repository/Run population behind an observed execution aggregate. Dependency evidence coverage is shown compactly as current complete snapshots versus repositories needing current evidence.
- **Repositories** — see managed repositories, run one repository's own CI/CD, or open that repository's observed dependency evidence.
- **Dependencies** — search observed packages, narrow by version/evidence/advisory facts, inspect why a repository matched, then **Select all matching** to freeze the complete server-side result as explicit repository IDs. **Absent from lock** and **No current dependency evidence** are also clickable server-owned populations; use the latter to select repositories for an explicit `python/dependency-inventory` Shared Plan when evidence needs refreshing.
- **Runs → New run** — choose a saved Plan and explicit repositories; backend verification updates automatically, then Run becomes available only for an entirely accepted selection. A dependency selection can preload the exact frozen IDs without creating a dependency-specific execution path.
- **Schedules** — choose one or more repositories, one saved Plan and a familiar frequency; each occurrence exposes independent repository Runs that open in Game Tape.
- **Runs** — opens as history without implicitly selecting the latest Run. Select the exact Run you want to review, then use **Overview → Timeline → Logs** and deeper Game Tape views as needed.
- **Scout** — inspect read-only workload/resource evidence; Scout never selects or executes dependency maintenance.

Settings and Help are utility controls. Recovery is reached from canonical failure guidance, and Recovery/Tools become top-level navigation in **Advanced** experience. Manual Verify, security, migration and command recipes remain Advanced work rather than the normal first-run path.

## CLI workflows

The CLI uses the same Python implementation as the GUI. After
`make backend-install` or `make init`, run it from the repository root:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon --help
```

Use that prefix for the `oh-goon` examples below. The optional `make cli-install`
target builds a standard wheel and requires the release owner's built UI. Source
CLI setup uses `make backend-install`. For contributor browser acceptance,
prepare browser engines separately after normal setup:

```bash
make browser-setup
# If preflight reports missing host libraries, install them explicitly (may require sudo/root):
make browser-system-deps
make browser-preflight
```

Useful advanced commands:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon doctor
uv run --offline --frozen --no-sync --project backend oh-goon local-state check        # read-only schema/integrity
uv run --offline --frozen --no-sync --project backend oh-goon local-state prelaunch-check  # adds write/lock/storage proof
uv run --offline --frozen --no-sync --project backend oh-goon local-state path
make architecture
```

### Select one workflow configuration

Initialize a trusted local project and run the generated workflow:

```bash
oh-goon init --template python/quality --repository /absolute/path/to/project --no-input
oh-goon run ./workflow --no-input
```

Initialization validates inputs, registers only the selected exact Git root if
needed, and publishes `run.yml` and its first canonical lock together. `--output`
defaults to `workflow`; an existing destination is refused. `--kind` defaults to
`plan`, while `--kind goon` selects a single Goon explicitly.
If interruption prevents the caller receiving the publication receipt, the
response reports publication as unverified. Inspect the destination and retained
registration facts before retrying; absence of a return value is not rollback.

For `python/quality`, the project needs `pyproject.toml` and native `uv.lock`;
its selected dependency group supplies Ruff, pytest and the chosen type checker.
Defaults select project `.`, group `dev` and Pyright. Supply caller choices at
initialization, for example `--input project=backend --input typechecker=ty`.
Prepare that project with native uv as usual. Ordinary verification uses trusted
local execution; strict setup applies only when the workflow selects it.

The v2 `target.repository` path is relative to `run.yml`'s directory, or explicitly
absolute. Repeating init's `--repository` writes an exclusive `target.repositories`
list for one Plan over at least two distinct roots; no implicit fleet is selected.
Repository Goons and Agent/repair workflows remain singular. Each path selects
the exact registered root, including an existing registered
filesystem subject without Git. It never guesses an ancestor Git root, enrolls
siblings or treats a parent folder as the selected non-Git application.

After reviewing later source/input changes, prepare the new lock explicitly:

```bash
oh-goon workflow prepare ./workflow
oh-goon run ./workflow
```

`templates list`, `workflow inspect ./workflow`, `workflow validate ./workflow`
and stateless `doctor` are optional diagnostics. Inspection/validation do not
register targets, write locks, start the app or execute checks. Source acquisition
requires explicit `--acquire`; preparing a fresh CI target requires explicit
`--register-target`. Run never registers, refreshes refs or repairs a lock.

Use [the configuration guide](development/TEMPLATE_INPUT_INSPECTION.md) for
file/repository/Git sources, typed inputs and explicit `init --draft` Agent/repair
authoring. `runs show|handoff|steps|outputs RUN_ID` reopens retained execution
facts; `runs logs RUN_ID` reports transient retention honestly. `app` owns browser
lifecycle commands. Shared Plan, Lineup and Schedule operations retain their
existing multi-repository and instance authority.

### Find and explain a previous Run

```bash
oh-goon runs list --limit 20 --json
oh-goon runs explain RUN_ID
oh-goon runs show --last --repository-id REPOSITORY_ID --plan-id PLAN_ID --json
```

The list supplies exact Run, Plan and repository identities from the selected
state root. Its `next_before_revision` continues the original admission-ordered
page through `--before-revision`; it does not sort by an inferred completion
time. `--last` requires both exact Plan and repository IDs from that history.
It never chooses a global latest Run or guesses a scope from the current
directory. A historical failed Run can be read successfully without granting
retry, repair or replay authority.

```bash
oh-goon runs logs RUN_ID --after 0 --json
oh-goon runs logs RUN_ID --after 0 --follow
```

JSON snapshots carry the original `next_sequence`, retained cursor and dropped
chunk count. Advance with that cursor. Text follow is available only while the
original live-output owner is active, and stops when it seals or becomes
unavailable. Ctrl-C stops following; it does not cancel the Run. A fresh CLI
after that owner closes can report `UNAVAILABLE`; use durable `runs explain`,
`runs events` or `runs handoff` for recorded facts. `--follow --json` refuses;
JSON consumers use bounded snapshots rather than an unbounded transcript.

### JSON and shell results

Each supported `--json` invocation publishes one JSON value on stdout, including
argument/configuration refusal and interruption. Owner schemas and Run/Step
facts remain intact. `_cli.schema` is `oh-goon.cli/v1`; `_cli.exit_code` and
`_cli.exit_kind` describe the CLI invocation, separately from recorded Step exit
codes or a historical Run outcome. The reply is bounded to 1 MiB. Oversized or
invalid presentation fails with a typed error and an action to select a smaller
page or bounded handoff; it does not invent successful evidence or alter a Run.
Runtime cleanup and private reply-staging cleanup are separate `_cli` observations.
An original successful Run can remain successful while CLI cleanup is unverified
and the invocation returns nonzero. Private staging settles before the final
reply is published; exception messages and private staging paths are withheld.

| Shell exit | Meaning |
| --- | --- |
| `0` | The command or evidence read succeeded. |
| `1` | The command reported non-success; for `run`, admitted execution was non-successful. |
| `2` | Arguments, configuration, startup or presentation were refused/failed. |
| `3` | Requested Run evidence or transient output is unavailable. |
| `130` / `143` | The invocation was interrupted by SIGINT / SIGTERM. |

Progress/operator hints use stderr. JSON uses ASCII escapes independently of
terminal width, locale, color or TTY. Framework diagnostics do not echo raw
exceptions, private task values or dotenv contents. The original execution
owner still applies its declared secret redaction; this is not a promise to
redact arbitrary application data deliberately published as outputs.

Basic `oh-goon doctor --json` reports native tools and selected state/config
paths without opening the operational database. `doctor --runtime --json`
explicitly observes an existing compatible runtime; it does not initialize,
repair or recover it. Guided native setup and its qualification limits are in
[Agent providers](AGENT_PROVIDERS.md#set-up-a-native-execution-profile).
