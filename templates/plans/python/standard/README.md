# python/standard

Calculate explicit release metadata, then run Python quality.

**Status:** runnable with prerequisites. **Effects:** Writes metadata and the test report; executes native checks.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. The locked group must also provide the project-owned `release-metadata` command; select the intended Git revision and channel.

```sh
oh-goon init --template python/standard --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: python/standard}
target: {repository: /absolute/path/to/project}
inputs:
  typechecker: ty
  release_channel: production
  revision: abc1234
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `project_version`, `release_version` and `junit.xml`. Metadata and build version authoring remain project-owned.
