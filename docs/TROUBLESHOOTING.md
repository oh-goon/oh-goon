# Troubleshooting

## Start here

| If you see this error | It means | Fix |
| --- | --- | --- |
| `command not found: oh-goon` | The shell has no global CLI command. | For the source UI use `make dev`. After `make init`, run source CLI commands with `uv run --offline --frozen --no-sync --project backend oh-goon` from the repository root. |
| `standard release wheel requires generated/release/static/index.html` | A standard wheel was built before the release UI payload existed, for example through `make cli-install`. | For development use `make init` and the source CLI above. Building a distributable wheel requires the release owner's UI build. |
| `The bundled oh-goon UI is not present` | `oh-goon start` was used from a source checkout instead of a built release. | Run `make dev` and open the exact `http://127.0.0.1:5173/#access=...` URL it prints. |
| `Startup stopped before opening the application.` | The local SQLite schema does not match the current application epoch. | For a source checkout run `make local-state-reset`. For a built release use the reset command printed by that same release. Do not use a different/global binary. |
| `uv` missing or environment validation fails | The source toolchain, committed dependency authority, or materialized local environment is not healthy. | Install the source prerequisites listed in [Quickstart](QUICKSTART.md), then run `make init`. After initialization, use `make diagnostic-system-check` to inspect the environment. |
| Hashmarks import or locked wheel download fails | The required Hashmarks dependency was not materialized successfully in the project environment. | Inspect the native uv failure and rerun `make init` after fixing access or configuration. A host-installed package, test substitute, or local identity fallback is not the required locked dependency. |

For source development, the normal startup path remains:

```bash
make init   # fresh or existing checkout; native uv/npm materialize committed locks
make dev
```

Open the exact `http://127.0.0.1:5173/#access=...` URL printed by `make dev`. Use `make diagnostic-system-check` only when you need diagnostics. `oh-goon start` is for built releases.

## Local database schema mismatch

oh-goon is still pre-live, so incompatible local SQLite epochs are deliberately recreated rather than migrated. Normal startup tells you the **exact configured database path** and the supported reset command. You do not need to find SQLite files manually.

Inspect without changing anything:

```bash
make local-state-check
make local-state-path
```

Reset stale local state from a source checkout:

```bash
make local-state-reset
```

For a built release, use `oh-goon local-state reset --yes` from that exact installed release. Never use an older/global CLI to repair a source checkout. Then restart through the same path you were using: `make dev` for source or `oh-goon start` for the built release.

The reset command works even when the normal application runtime cannot start. It removes only that database plus its SQLite `-wal`, `-shm`, and `-journal` sidecars. It does **not** remove repositories, working trees, project files, retained source content, or tool caches. The command requires `--yes`; without it, it prints exactly what would be deleted and changes nothing.

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

## Generated API files are stale

```bash
make generate
make check-generated
```


Saved League folders are kept separately in `league.yml`; resetting the disposable SQLite database does not remove them.
