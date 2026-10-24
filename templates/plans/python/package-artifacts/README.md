# python/package-artifacts

Build the project’s distribution bundle and retain it for independent retrieval.

**Status:** runnable with prerequisites. **Effects:** Executes packaging and publishes artifact bytes.

**Prerequisites:** A committed Python project and uv lock. The locked group must provide the project-owned `package-artifacts` command; retained distribution bytes require configured content-store publication. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init python/package-artifacts --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/package-artifacts}
target: {repository_id: REPOSITORY_ID}
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `package-distributions.tar` uses content-store retention; `package-artifacts.json` is evidence. The project command owns packaging semantics.
