# Local-first application contract

oh-goon is designed to be an ordinary local application, not a hosted control plane.

## Local state paths

Oh Goon keeps operating-system **data** and **configuration** authority separate. The database chooses the data root; `league.yml` chooses the configuration root. Do not merge those rules into one generic platform-base helper.

| State | Linux default | macOS default | Windows default | Override / derivation |
| --- | --- | --- | --- | --- |
| Operational database | `$XDG_DATA_HOME/oh-goon/oh-goon.db` or `~/.local/share/oh-goon/oh-goon.db` | `~/Library/Application Support/oh-goon/oh-goon.db` | `%LOCALAPPDATA%\oh-goon\oh-goon.db` | `OH_GOON_DB` |
| SQLite sidecars | beside database: `oh-goon.db-wal`, `oh-goon.db-shm`, `oh-goon.db-journal` when SQLite creates them | same | same | derived from exact database path |
| `extensions/` | beside database | beside database | beside database | derived from database parent |
| `plan-tools/` | beside database | beside database | beside database | derived from database parent |
| `content-store/` | beside database | beside database | beside database | derived from database parent |
| `run-workspaces/` | beside database | beside database | beside database | derived from database parent |
| `league.yml` | `$XDG_CONFIG_HOME/oh-goon/league.yml` or `~/.config/oh-goon/league.yml` | `~/Library/Application Support/oh-goon/league.yml` | `%APPDATA%\oh-goon\league.yml` | `OH_GOON_LEAGUE_FILE`; if only `OH_GOON_DB` is set, defaults beside that database |
| `lineups/` | beside `league.yml` | beside `league.yml` | beside `league.yml` | `OH_GOON_LINEUP_DIR` |
| `plans/` | beside `league.yml` | beside `league.yml` | beside `league.yml` | `OH_GOON_SHARED_PLAN_DIR` |
| Scout analytical DB | beside operational DB as `oh-goon.scout.db` | same | same | derived from exact operational DB path; disposable analytical state |

`ApplicationStatePathLayout` owns the DB-adjacent `extensions/`, `plan-tools/`, `content-store/`, and `run-workspaces/` layout. League preferences, Lineups, and Shared Plans remain configuration-owned. Scout owns a separate disposable analytical SQLite family and never becomes canonical execution state.

On POSIX, Oh Goon actively re-hardens its canonical data/configuration roots to `0700` and sensitive state files to `0600`; it does not rely on the umask or on the permissions an older checkout happened to create. Exact-file coordination locks use a per-OS-user private temporary root, so another local user cannot share or pre-seed the same lock namespace. Native-Windows defaults remain under the current user profile ACL; custom overrides must provide an equivalently private ACL.

## Everyday startup

A built release contains the React application inside the Python package. Normal startup is:

```bash
oh-goon start
```

The command binds only to `127.0.0.1:8787`, creates a process-local 256-bit access capability, prints the exact `#access=...` launch URL and saved League preference path, then serves both API and UI from one process. The browser exchanges that fragment for an HttpOnly `SameSite=Strict` cookie and removes the fragment before rendering normal workflows. Node, npm and Vite are contributor/build-time tools only.

`oh-goon serve` remains the lower-level API/server command. Contributor source checkouts use `make dev` when they want Vite hot reload.

## What survives between uses

There are two different classes of local state:

1. **League preference** — the folders the user chose. These are durable user preferences stored in `league.yml` and should survive normal restarts and disposable runtime-database resets.
2. **Operational state** — run history, Journal facts, leases and runtime projections. These live in SQLite and follow the current pre-live database epoch.

The default League preference file is platform-local configuration (`~/.config/oh-goon/league.yml` on a typical Linux system). `OH_GOON_LEAGUE_FILE` may explicitly choose another file. When `OH_GOON_DB` is explicitly overridden and no League-file override is supplied, `league.yml` lives next to that database so isolated local instances stay isolated.

The format is intentionally tiny and human-readable:

```yaml
version: 1
folders:
  - /home/me/code
  - /home/me/work
```

Teams are **not** persisted into this file. At startup/read time oh-goon scans the saved folders and derives current Git Teams and root `goon.yml` status from the filesystem. This prevents a stale second Team registry.

## SQLite is a runtime projection, not League preference authority

The existing `locations` SQLite table remains the transactionally consistent runtime projection used by Fleet admission/read views. On startup, oh-goon reconciles that projection from `league.yml` before recovery/admission begins.

Ordinary startup never promotes the disposable SQLite projection into `league.yml`. A missing `league.yml` means the user currently has no configured League folders, so startup clears any stale SQLite location projection before admission. The explicit local-state reset deletes the incompatible operational database only. It never reads obsolete SQLite rows to reconstruct current preferences; `league.yml` is the sole durable League-folder authority.

Before `make dev`, `oh-goon start`, or `oh-goon serve` launches application processes, Oh-Goon runs `local-state prelaunch-check`. This is intentionally stronger than the manual read-only `local-state check`. For an existing database, the prelaunch proof opens that exact file non-creating (`mode=rw`), acquires `BEGIN IMMEDIATE`, and rolls back without semantic mutation; this proves SQLite can create/acquire the write/journal locking it will need. If the database does not yet exist, the real path remains absent while Oh-Goon creates a disposable sibling SQLite database, commits/reopens/checks it, and removes it and its sidecars. Failure to write, lock, commit, reopen, integrity-check, or clean up stops startup before uvicorn/Vite. SQLite remains format authority; this is storage admission evidence, not repair or migration.

Normal add/remove writes must not publish `league.yml` and the SQLite admission projection independently. `LocalState` is the only read-modify-write owner; `LeagueFolderStore` exposes only read plus whole-value replacement. Runtime mutation and startup reconciliation hold the exact `league.yml` file lock across their SQLite transaction, so cooperating maintenance/file writers cannot interleave between preference observation, projection preparation, publication, commit or compensation. The projection is prepared first, then `league.yml` is atomically replaced. If the transaction response is ambiguous after publication, `LocalState` rereads the committed SQLite projection while still holding that file lock: the intended projection proves the mutation committed and keeps the new preference, while the previous projection proves rollback and permits restoring the previous preference. An unverified or unexpected projection fails closed instead of promoting disposable SQLite state into `league.yml`. Startup reconciliation uses the same one-way proof: if its SQLite commit response is lost, it returns success only when a fresh projection exactly equals the locked `league.yml` tuple; otherwise the startup error remains visible. Tests must compare committed reads and restart/reload state, not a mutable in-memory shadow.

## Reset behavior

For a source checkout, `make local-state-reset` deletes only the disposable SQLite database and its sidecars. `league.yml` remains. The next `make dev` recreates SQLite and restores the League-folder runtime projection automatically.

For a built release, use `oh-goon local-state reset --yes` from that exact release and then `oh-goon start`. This is explicit rather than an automatic destructive migration, and the source and built-release binaries must not be mixed.

## Offline boundary

Starting and browsing the local app does not require network access. Network authority belongs to explicit CI/CD steps that declare egress; merely starting oh-goon or scanning the League never downloads tools, contacts a cloud service, executes `goon.yml`, or mutates a Team repository.

## Local Team runs

The normal CI/CD action is one click from the League map: **Run team**. Only a Team whose exact repository-root `goon.yml` is structurally configured gets that action. Pressing it does not execute the scan result; the effectful boundary resolves the current `goon.yml` again and admits exactly that Team through the same FleetRun lifecycle used by history, cancellation, Enforcer receipts and recovery.

The command-line equivalent is:

```bash
oh-goon team run /path/to/team
```

The local CLI stays attached until the durable FleetRun is terminal. This is intentionally different from the browser API, which is asynchronous because the local app process remains alive and watches the same run. There is no second local-run state machine.

## Shared Plans and Shared Runs

For repeated cross-repository checks, oh-goon can own a normal `plan.yml` as a Shared Plan. Shared Plans are stored outside Team repositories under local oh-goon configuration and survive operational SQLite resets.

A run chooses **one source before resolution**:

```text
Team run      -> <git-root>/goon.yml
Shared Run     -> oh-goon plans/<key>/plan.yml
```

When a Shared Plan is chosen, repository `goon.yml` files are omitted completely for that Shared Run. They are not read by execution target selection, resolved, validated, merged, inherited, executed, or used as fallback. Therefore a selected Git Team may participate even when its own `goon.yml` is missing or invalid.

The browser **Plans** page is the single saved-Plan authoring surface. Shared Runs select one saved Plan plus current League Teams and start the same canonical FleetRun path; Schedules also consume saved Plans rather than authoring them. CLI parity is provided by `oh-goon shared-plan list|save|run`. See [`development/SHARED_PLANS.md`](development/SHARED_PLANS.md).

## Reusable Lineups

For repeated Shared Runs, save a **Lineup** instead of re-selecting the same Teams each time. A Lineup remembers only exact managed Team roots and survives restarts/operational DB resets as local configuration. It does not auto-run anything and it does not inspect or inherit repository `goon.yml` files.

The browser requests the read-only Shared Plan preview automatically whenever the selected Plan or repositories change; `Run` remains disabled until the latest exact selection is accepted. CLI users can request the same check with `oh-goon shared-plan preview <plan> --lineup <lineup>`. A moved or removed saved repository is shown as unavailable and must be fixed explicitly; oh-goon never silently substitutes another repository.


## Container persistence boundary

The supported Compose runtime mounts one writable host directory at `/var/lib/oh-goon`. The canonical SQLite database (`OH_GOON_DB=/var/lib/oh-goon/oh-goon.db`) and `league.yml` therefore survive container replacement together, while SQLite WAL/SHM files remain beside the database on the same filesystem. This is the container persistence contract; Oh Goon does not own backup archives, retention or restore tooling. Operators/platforms own backups of the mounted directory when required.
