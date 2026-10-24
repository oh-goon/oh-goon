# Quickstart

You do not need to understand SQLite, the Journal, capabilities, or operation schemas to use oh-goon.

## 1. Standard start — recommended

For a source checkout, use the browser-first path:

```bash
make init   # native uv sync + native npm ci + tiny Oh-Goon smoke
make dev
```

Open the exact `http://127.0.0.1:5173/#access=...` URL printed by `make dev`. The Vite frontend proxies `/api` to the backend on `127.0.0.1:8787`; the one-time fragment establishes the process-local session and is removed from browser history. Python `>=3.12,<3.15` is resolved by uv; source checkouts require a Linux/WSL shell with Git, Bash, GNU Make, uv `>=0.10.0`, Node 22.16.0+, and npm 10.9.2+. `make diagnostic-system-check` is available when you need read-only environment and lock diagnostics, but it is not a startup prerequisite.

Hashmarks is required and installed by `make init` from the normal backend
dependency lock. No separate Hashmarks installation or agentsCookbook checkout
is needed. A native dependency failure must be resolved before running the
affected workload; diagnostics do not replace the real Hashmarks integration.

A new operational database is created automatically. If a pre-live schema change made the old local database incompatible, follow the exact recovery command printed by startup and then restart with `make dev`.

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

`oh-goon start` is for built releases only. A built release already contains the React UI, so Node/npm are not runtime dependencies:

```bash
oh-goon start
```

Open the exact `http://127.0.0.1:8787/#access=...` URL printed by `oh-goon start`. Oh Goon binds only to loopback and also requires the process-local access capability; loopback alone is not treated as authorization. No cloud account or remote control plane is required.

If you are in a source checkout and see `The bundled oh-goon UI is not present`, use `make dev` instead.

## 3. Add a League folder

On **Repositories**, choose the parent folder containing your repositories. That saved root is a **League folder** and remains saved on this computer between starts. oh-goon scans beneath it for Git roots and shows every discovered repository as a **Repository**.

The canonical repository CI/CD entrypoint is exactly `<git-root>/goon.yml`. A repository without it remains visible as **Missing**; an unsafe/unstable entrypoint remains visible as **Invalid**. Scanning is read-only and never executes the file.

## 4. Run a repository locally

For a repository marked ready, press **Run CI/CD** in Repositories. oh-goon resolves that repository's current root `goon.yml`, runs the normal Enforcer admission, then executes exactly that repository through the canonical local FleetRun lifecycle.

The browser run waits for the same canonical lifecycle and shows failure/cancellation in Runs and Game Tape. A Git repository does not need `pyproject.toml` merely to be a CI/CD target; language-specific files are requirements declared by its own `goon.yml`. CLI equivalents are collected in the **Advanced: CLI access** section at the end.

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

The same operations are available to power users through the optional CLI; see **Advanced: CLI access** below.

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

## Advanced: CLI access

The CLI is optional and uses the same Python implementation as the GUI. It is not required for the standard browser path.

After `make init`, run the source CLI from the repository root:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon --help
```

Use that prefix for the `oh-goon` examples below. The optional `make cli-install`
target builds a standard wheel and requires the release owner's built UI, so
it is not part of fresh-checkout startup. For contributor browser acceptance,
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

Register your repository in the UI, then use its repository ID in the generated
selection:

```bash
oh-goon templates list --kind plan
oh-goon templates init python/quality --output ./workflow
# Edit workflow/run.yml: target.repository_id and relevant inputs.
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

The config chooses a shipped Plan directly, so an unchanged Plan needs no local
copy. `inputs` supplies scalar values to the Goon-owned typed contract. Native
uv/Ruff/Pytest configuration stays in your project. Inspection is read-only;
validation is not execution, and a lock is not write permission. Source or
selection changes require a new matching lock and fresh admission.

Use the [configuration guide](development/TEMPLATE_INPUT_INSPECTION.md) for
project Plans, repository Goons and optional agent/repair groups. Shared Plan
UI/service operations retain explicit multi-repository selection and their own
reviewed identity. The removed Plan-path/values/flag assembly has no compatibility
route.
