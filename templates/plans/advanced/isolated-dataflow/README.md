# advanced/isolated-dataflow

Select project preparation, tests, build and publication scripts with explicit output/environment transfer.

**Status:** teaching example. **Effects:** Runs project commands and can publish through `ci/publish.sh`.

**Prerequisites:** Project-owned `ci/prepare.sh` and `ci/publish.sh`, native uv with pytest/pytest-timeout, build prerequisites and explicitly admitted `RELEASE_TOKEN`. Read the scripts before using them.

```sh
oh-goon init --template advanced/isolated-dataflow --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: advanced/isolated-dataflow}
target: {repository: /absolute/path/to/project}
inputs:
  tag: dev
  test_timeout_seconds: 600
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `RELEASE_TAG` from the prepare Step and native Step outcomes; project scripts own other products.

The [project fixture](../../../../template-certification/fixtures/advanced/isolated-dataflow/) implements this workflow with real pytest/pytest-timeout tests and native `uv build`. Its preparation command validates and publishes the selected label. The last command receives the label with `-beta` appended and the explicitly imported `RELEASE_TOKEN`, then exports the built wheel and source archive to `published/<label>-beta/`. That fixture performs local file publication and makes no registry request. Use a disposable non-secret token for its local permission check.

The Plan keeps ordinary local verification policy. Its name grants no strict isolation; a workload publishing to an external system needs that system's explicit execution and release authority.
