# python/secure

Run Python quality, then uv and Trivy vulnerability scanners.

**Status:** runnable with prerequisites. **Effects:** Runs native checks, makes declared online queries and retains the Trivy report.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. Native uv vulnerability audit support. Native Trivy and its declared online egress; configured strict content-store publication for the report. A missing scanner or unavailable network cannot establish a scan result. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init python/secure --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/secure}
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

**Expected outputs:** JUnit evidence, native uv audit output and retained `trivy-audit.json`. A nonzero scanner result fails its Step.
