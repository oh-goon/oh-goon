# python/release-candidate

Calculate candidate metadata, run Python quality and retain distribution artifacts.

**Status:** runnable with prerequisites. **Effects:** Writes metadata, runs checks and packages retained bytes.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. The locked group must also provide the project-owned `release-metadata` command; select the intended Git revision and channel. The locked group must provide the project-owned `package-artifacts` command; retained distribution bytes require configured content-store publication. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init python/release-candidate --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/release-candidate}
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

**Expected outputs:** Release metadata, JUnit evidence, retained distribution bundle and package manifest. Building uses the project’s existing version.
