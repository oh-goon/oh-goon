# Troubleshooting

## Start here

| If you see this error | It means | Fix |
| --- | --- | --- |
| `command not found: oh-goon` | The shell has no global CLI command. | For the source UI use `make dev`. After `make init`, run source CLI commands with `uv run --offline --frozen --no-sync --project backend oh-goon` from the repository root. |
| `standard release wheel requires generated/release/static/index.html` | A standard wheel was built before the release UI payload existed, for example through `make cli-install`. | For development use `make init` and the source CLI above. Building a distributable wheel requires the release owner's UI build. |
| `The bundled oh-goon UI is not present` | `oh-goon app start` was used from a source checkout instead of a built release. | Run `make dev` and open the exact `http://127.0.0.1:5173/#access=...` URL it prints. |
| `Startup stopped before opening the application.` | The local SQLite schema does not match the current application epoch. | Preserve the store; use its matching original runtime to settle active or unresolved ownership, then follow the cutover guidance below. A reset refuses incompatible or unresolved state. |
| `uv` missing or environment validation fails | The source toolchain, committed dependency authority, or materialized local environment is not healthy. | Install the source prerequisites listed in [Quickstart](QUICKSTART.md), then run `make init`. After initialization, use `make diagnostic-system-check` to inspect the environment. |
| Hashmarks import or locked wheel download fails | The required Hashmarks dependency was not materialized successfully in the project environment. | Inspect the native uv failure and rerun `make init` after fixing access or configuration. A host-installed package, test substitute, or local identity fallback is not the required locked dependency. |

For source development, the normal startup path remains:

```bash
make init   # fresh or existing checkout; native uv/npm materialize committed locks
make dev
```

Open the exact `http://127.0.0.1:5173/#access=...` URL printed by `make dev`. Use `make diagnostic-system-check` only when you need diagnostics. `oh-goon app start` is for built releases.

## Local database schema mismatch

Incompatible SQLite generations are refused without migration, conversion or automatic reset. Normal startup tells you the **exact configured database path**. Preserve its database, sidecars and ownership evidence; the new runtime cannot safely decide whether an older store has an unresolved writer.

Inspect without changing anything:

```bash
make local-state-check
make local-state-path
```

`oh-goon doctor --json` remains available when the database is missing, corrupt,
incompatible or not writable. It lists the selected DB, League, provider and
application-state paths and native tool facts without opening that database or
reading private configuration bodies. `oh-goon doctor --runtime --json` is a
separate observation of existing state: a `STATE_*` refusal preserves the store,
and a successful observation uses the original runtime health findings. Neither
doctor command authorizes reset, repair or recovery. Native version probes have
a five-second deadline and bounded output; a stalled/noisy tool is reported as
broken rather than blocking diagnosis indefinitely.

Stop new admission and use the matching original runtime's lifecycle/recovery owners to settle active Runs, incomplete campaigns, leases, workspace owners and uncertain effects. If any ownership remains unresolved, cutover stays blocked. After resolution, preserve the old state and schema metadata read-only outside the active state directory, then explicitly select a fresh generation. See the [execution-state cutover contract](development/AGENT_RUNTIME_OWNERS.md#execution-state-cutover-and-recovery).

For compatible state whose ownership is fully settled, a source checkout can explicitly clear its disposable history:

```bash
make local-state-reset
```

For that same compatible, settled case in a built release, use `oh-goon local-state reset --yes` from the exact installed release. Do not use a different/global binary to bypass a refusal. Then restart through the same path: `make dev` for source or `oh-goon app start` for the built release.

The reset owner validates the selected compatible store read-only under the original SQLite gate and refuses incompatible, corrupt, active or unresolved state before deletion. A permitted reset removes only that database and its SQLite `-wal`, `-shm`, and `-journal` sidecars. Repositories, working trees, project files, retained source content and tool caches remain. The command requires `--yes`; without it, it prints the selected files and changes nothing. Confirmation cannot override unresolved ownership.

If `OH_GOON_DB` is set, the same commands use that configured path automatically.

## A repository needs attention

Use the Fleet Attention Center. Expand **Technical details** only when needed, fix the repository, then **Recheck affected**.

## A run looks stuck

Open the current run and cancel it. Execution cancellation terminates the child process group and releases the SQLite execution lease.

For contributor diagnosis:

```bash
uv run --locked --project backend oh-goon dev explain-run <run-id>
make architecture
```

For CLI readback, start with `oh-goon runs list`, then use
`oh-goon runs explain RUN_ID`. Exact scoped `--last` requires both Plan and
repository identities from that list in the selected state root. `runs logs`
exit `3` means its requested transient evidence is unavailable, not that the
historical Run failed. `runs handoff RUN_ID --json` retains durable outcome,
uncertainty and safe navigation facts. Stopping text log follow with Ctrl-C is
read-only and does not request Run cancellation.

## Generated API files are stale

```bash
make generate
make check-generated
```


Saved League folders are kept separately in `league.yml`; resetting the disposable SQLite database does not remove them.
