# python/profile

Profile the project’s real pytest workload with Pyinstrument.

**Status:** runnable with prerequisites. **Effects:** Executes tests and writes an HTML profile.

**Prerequisites:** A committed Python project and uv lock; its selected group must supply pytest and Pyinstrument.

```sh
oh-goon init --template python/profile --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: python/profile}
target: {repository: /absolute/path/to/project}
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `pytest-profile.html` is evidence. The project’s native pytest configuration owns discovery.
