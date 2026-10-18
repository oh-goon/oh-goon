# League discovery and `goon.yml`

## Purpose

oh-goon needs one boring, obvious answer to two questions:

1. Which local repositories are Teams in this League?
2. Which CI/CD file belongs to each Team?

The durable configuration is the set of **League folders** selected by the developer. Everything below that is observed from the local filesystem.

## The model

```text
Saved League folders
        │
        ├── /work/platform
        └── /work/services
                 │
                 ▼
       scan Git repository roots
                 │
                 ▼
               Teams
                 │
                 ▼
       <git-root>/goon.yml
```

A Git repository root is a Team whether or not it currently has `goon.yml`. This is useful because the League map can show which Teams still need CI/CD configuration.

Discovery walks descendants of the configured League root until a Git root is found, records that Team, and stops descending inside that repository. Incidental ancestor names do not redefine Team identity: `build/repo/.git`, `dist/repo/.git`, `.hidden/repo/.git`, `node_modules/repo/.git`, and `workspace/repo/.git` are equivalent Git-root semantics. Only Git administrative state itself is excluded from traversal. If narrower traversal is ever needed, it must be an explicit user-owned scope policy, not a hidden name list.

Only the exact regular file `<git-root>/goon.yml` is the canonical repository-owned CI/CD entrypoint. There is no `goon.yaml` fallback, recursive search, nearest-parent search, nested override, or filename guessing.

## Why Team state is not persisted

The saved League folders are user configuration and therefore durable. The Team catalog is not.

Persisting discovered Teams would create two truths:

- what the database says exists;
- what the filesystem actually contains now.

Instead, every League scan derives Teams from the saved roots. Creating/removing a repository or adding/removing its root `goon.yml` is reflected by the next scan without a catalog synchronization job.

## Discovery is read-only

Scanning does not resolve or execute a pipeline. It only identifies Git roots and classifies the root entrypoint as:

- `CONFIGURED` — stable regular `goon.yml` bytes were observed and SHA-256 fingerprinted;
- `MISSING` — no root `goon.yml` exists;
- `INVALID` — the entrypoint is a symlink, non-file, unreadable, or changed while being observed.

The scanner opens the exact root file without following symlinks where the platform supports `O_NOFOLLOW`, verifies file identity before and after reading, and reports an invalid observation if the file changed during the scan.

## Explicit validation

Discovery and validation are different actions:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon league scan
uv run --offline --frozen --no-sync --project backend oh-goon team validate /work/services/payments
```

`team validate` resolves that Team's exact root `goon.yml` through the same Fleet Plan resolver used elsewhere. It still does not execute the plan.

`goon.yml` is therefore analogous to `.gitlab-ci.yml`: repository-owned CI/CD source by convention. It is not a browser-owned pipeline model and is not executed merely because a folder was scanned.

For a new Python/uv Team, start by copying [`templates/goons/python/quality/goon.yml`](../../templates/goons/python/quality/goon.yml) to the repository root. That starter requires `pyproject.toml`, `uv.lock`, and `Dockerfile` before admission, then performs only lock consistency, one explicit locked dependency sync, Ruff format/lint, and Astral ty.

## Current boundary

Phases 959–968 establish discovery, identity, safe fingerprinting, API/CLI surfaces, and explicit validation. The existing Fleet runner still has a shared-plan target-selection shape. A later phase must deliberately change execution ownership before oh-goon can truthfully offer “run every Team's own `goon.yml`.”

Do not bypass that change by resolving one Team's file and silently applying it to all selected repositories.

## No-magic rules

- Persist League folders, not a discovered Team shadow catalog.
- Git root defines Team identity.
- Only root `goon.yml` defines the default Team CI/CD entrypoint.
- Do not search recursively for alternative pipeline files.
- Do not infer `goon.yml` from `pyproject.toml` or vice versa.
- Do not execute, acquire runtimes, import secrets, or mutate repositories during League scan.
- Validation is an explicit action.
- Missing/invalid entrypoints remain visible facts rather than being silently skipped.
