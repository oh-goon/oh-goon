# Getting started in the GUI

oh-goon is designed so a junior developer can perform normal fleet maintenance without understanding its internal state model.

## The primary pages

### Repositories

Your repository inventory and repository-owned CI/CD workspace. Add League folders, inspect current repository health and each root `goon.yml` state, then use **Run CI/CD** for that repository's declared work.

### Shared Runs — Runs → New run

Run one explicitly selected Shared Plan over one or more explicitly selected repositories. A Shared Run never reads, merges, inherits, or falls back to the selected repositories' own `goon.yml` files.

### Schedules

Inspect time triggers. Each Schedule owns an explicit repository target set, one Plan, and wall-clock timing; it is not a workflow composer. Each repository target becomes its own canonical Run.

### Runs

Inspect active and historical execution through the canonical Run/Game Tape evidence path.

### Tools

Special-purpose maintenance and authoring tools. Advanced mode reveals lower-level diagnostics and maintenance capabilities without changing execution semantics.

### Settings

Manage League folders saved on this computer, language, browser notifications, and Standard/Advanced experience mode. Technical diagnostics are collapsed under Advanced.

### Help

Task-oriented explanations for checks, updates, issues, results, and review preparation.

## Standard mode is intentional

Standard mode hides information that is useful for debugging the application but unnecessary for normal dependency maintenance. It does not remove safety checks or change backend behavior.

Backend state remains canonical. The UI only changes presentation and selection of repositories for the next action.

## If oh-goon starts in degraded safe mode

oh-goon may keep the UI/API available while blocking mutations if startup reconciliation or invariant proof finds unsafe durable state. Open **Recovery** or inspect **Settings → Advanced** to see the invariant codes and repair-plan guidance. Reconcile/inspect the evidence; do not delete Journal facts or force-release a lease just to clear the warning.

Once the proof is healthy, manual operations can proceed. Restart oh-goon to resume periodic Schedule evaluation after a degraded startup. This restart requirement is intentional: recovery inspection never silently restarts background mutation behavior.

## If startup says the local database is from an older epoch

Normal startup refuses an incompatible SQLite generation and prints its exact path. Preserve that state and use its matching original runtime to settle active or unresolved execution ownership. After ownership is resolved, archive the old state and schema metadata read-only, then explicitly select a fresh generation. See [schema-mismatch recovery](TROUBLESHOOTING.md#local-database-schema-mismatch); choosing a new directory or confirming reset cannot clear an unresolved writer.

Inspect from the source checkout:

```bash
make local-state-check
make local-state-path
```

For compatible state with fully settled ownership, `make local-state-reset` can explicitly clear source-checkout history. The built-release equivalent is `oh-goon local-state reset --yes` from that exact release. Reset refuses incompatible, corrupt, active or unresolved state. A permitted reset removes only the selected SQLite database and sidecars; repositories and project files remain, and saved League folders stay in `league.yml`. Restart through `make dev` or that same release's `oh-goon app start` after the selected recovery completes.


## Reusable Shared Plans

For checks you want to repeat across repositories, save a normal `plan.yml` as a Shared Plan rather than copying execution definitions into every repository. Creating from a shipped Plan template is explicit, and the saved Plan survives restarts and disposable DB resets.

Shared Plan editing and multi-repository selection remain explicit UI/service
operations. Normal CLI setup selects a registered target in one `run.yml`; it
never infers a fleet from an empty target or merges a target's repository Goon.

## Select and run a workflow

```bash
oh-goon init --template python/quality --repository /absolute/path/to/project --no-input
oh-goon run ./workflow --no-input
```

Initialization validates typed caller values, explicitly registers the selected
exact Git root when needed, and publishes a v2 path-selected `run.yml` together
with the first canonical `goon.lock`. Supply nondefault values with
`--input NAME=VALUE`; the compiler owns conversion and constraints.

The CLI waits for the original terminal outcome. Optional `workflow inspect`
and `workflow validate` remain read-only by default. After changing the selected
source or inputs, review them and use `workflow prepare ./workflow` before Run.
Fresh admission still owns permissions, target-instance and source freshness.
Agent/repair authoring uses explicit `init --draft`, then completion and prepare;
see [run configuration](development/TEMPLATE_INPUT_INSPECTION.md) and
[Agent Skills](AGENT_SKILLS.md).

Internal APP/release and enrolled deployment flows use their original
owner-controlled frontdoors; the ordinary selection file does not grant their
privileged custody or turn a Run result into a certification claim.

## Inspect an admitted Fleet Run (RUN-2)

After a `run run.yml --json` returns the `fleet_run_id`, query the
existing journal/Game Tape owners without re-running the Plan:

```bash
oh-goon runs show ENROLLED_RUN_ID --json
oh-goon runs events ENROLLED_RUN_ID --after 0 --json
oh-goon runs logs ENROLLED_RUN_ID --after 0 --json
```

For the next call to `events` or `logs`, pass the original `next_sequence` as
`--after`. `events` is the durable lifecycle journal; `logs` is retained
transient output and can return `state: UNAVAILABLE`. Never interpret missing
live output as missing execution or as proof of physical containment.

## Navigate archived Steps and outputs (RUN-4)

Agents can follow the same enrolled `fleet_run_id` from RUN-1/RUN-2/RUN-3
into the original archived Game Tape and its stable repository/Step identities:

```bash
oh-goon runs tape ENROLLED_RUN_ID --json
oh-goon runs steps ENROLLED_RUN_ID --json
oh-goon runs outputs ENROLLED_RUN_ID --json
```

These views are derived exclusively from `FleetRunDetail.game_tape` through
one `runtime.runs.get` read per request. Steps retain the original `why`,
`failure`, `step_execution_id`, and `issue_focus` fields; outputs retain the
original published output/artifact and stdout/stderr **hash/size evidence**.
`runs outputs` does not retrieve raw log bytes; `runs logs` is transient and
may be `UNAVAILABLE`. Nothing here replays a Run, grants admission, or certifies
physical containment. JSON `runs tape` can be large; use `runs steps` or
`runs outputs` for focused agent inspection. No new Hashmarks dependency.

## Resume a bounded journal event page (RUN-3)

The original journal owner can bound the durable events returned in one page.
The Plan CLI defaults to 100 events, and `--limit` accepts 1 to 100:

```bash
oh-goon runs events ENROLLED_RUN_ID --after 0 --limit 50 --json
# When has_more is true, call again with --after <next_sequence>.
```

`has_more`, `next_sequence`, and `journal_revision` all come from the same
immutable journal read cut. For a nonempty page, `next_sequence` is the last
delivered event—not the final event the journal knows about. If `has_more` is
false, this read cut is exhausted; later commits can still create new events.
The HTTP Fleet Run event endpoint also accepts optional `limit=1..100`, but
omitting it preserves the previous unbounded behavior for existing clients.
This bounds the *returned event projection*, not total journal disk/index work.
