# python/quality

Check formatting, lint, one selected type checker and tests using native project configuration.

**Status:** runnable with prerequisites. **Effects:** Reads project source and writes the declared test report.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project.

```sh
oh-goon init --template python/quality --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: python/quality}
target: {repository: /absolute/path/to/project}
inputs:
  typechecker: ty
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `junit.xml` in the selected project is evidence. Native Ruff/type-checker/pytest exits determine Step outcomes.
