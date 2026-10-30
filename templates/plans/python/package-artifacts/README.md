# python/package-artifacts

Build the project’s distribution bundle and retain it for independent retrieval.

**Status:** runnable with prerequisites. **Effects:** Executes packaging and publishes artifact bytes.

**Prerequisites:** A committed Python project and uv lock. The locked group must provide the project-owned `package-artifacts` command; retained distribution bytes require configured content-store publication. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon init --template python/package-artifacts --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: python/package-artifacts}
target: {repository: /absolute/path/to/project}
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `package-distributions.tar` uses content-store retention; `package-artifacts.json` is evidence. The project command owns packaging semantics.
