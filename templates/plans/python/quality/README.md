# python/quality

Check formatting, lint, one selected type checker and tests using native project configuration.

**Status:** runnable with prerequisites. **Effects:** Reads project source and writes the declared test report.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project.

```sh
oh-goon templates init python/quality --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/quality}
target: {repository_id: REPOSITORY_ID}
inputs:
  typechecker: ty
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `junit.xml` in the selected project is evidence. Native Ruff/type-checker/pytest exits determine Step outcomes.
