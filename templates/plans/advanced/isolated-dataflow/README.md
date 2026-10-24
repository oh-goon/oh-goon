# advanced/isolated-dataflow

Select project preparation, tests, build and publication scripts with explicit output/environment transfer.

**Status:** teaching example. **Effects:** Runs project commands and can publish through `ci/publish.sh`.

**Prerequisites:** Project-owned `ci/prepare.sh` and `ci/publish.sh`, native uv with pytest/pytest-timeout, build prerequisites and explicitly admitted `RELEASE_TOKEN`. Read the scripts before using them.

```sh
oh-goon templates init advanced/isolated-dataflow --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: advanced/isolated-dataflow}
target: {repository_id: REPOSITORY_ID}
inputs:
  tag: dev
  test_timeout_seconds: 600
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `RELEASE_TAG` from the prepare Step and native Step outcomes; project scripts own other products.
