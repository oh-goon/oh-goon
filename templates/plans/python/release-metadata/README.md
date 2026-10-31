# python/release-metadata

Calculate and expose release metadata through the project’s own command.

**Status:** runnable with prerequisites. **Effects:** Writes metadata files; the project command owns its effects.

**Prerequisites:** A committed Python project and uv lock. The locked group must also provide the project-owned `release-metadata` command; select the intended Git revision and channel.

```sh
oh-goon init --template python/release-metadata --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: python/release-metadata}
target: {repository: /absolute/path/to/project}
inputs:
  release_channel: production
  revision: abc1234
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `project_version` and `release_version` outputs. Calculation does not stamp a new version into source.
