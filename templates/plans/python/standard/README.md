# python/standard

Calculate explicit release metadata, then run Python quality.

**Status:** runnable with prerequisites. **Effects:** Writes metadata and the test report; executes native checks.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. The locked group must also provide the project-owned `release-metadata` command; select the intended Git revision and channel.

```sh
oh-goon templates init python/standard --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/standard}
target: {repository_id: REPOSITORY_ID}
inputs:
  typechecker: ty
  release_channel: production
  revision: abc1234
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `project_version`, `release_version` and `junit.xml`. Metadata and build version authoring remain project-owned.
