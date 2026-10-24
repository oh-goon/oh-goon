# python/release-metadata

Calculate and expose release metadata through the project’s own command.

**Status:** runnable with prerequisites. **Effects:** Writes metadata files; the project command owns its effects.

**Prerequisites:** A committed Python project and uv lock. The locked group must also provide the project-owned `release-metadata` command; select the intended Git revision and channel.

```sh
oh-goon templates init python/release-metadata --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/release-metadata}
target: {repository_id: REPOSITORY_ID}
inputs:
  release_channel: production
  revision: abc1234
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `project_version` and `release_version` outputs. Calculation does not stamp a new version into source.
