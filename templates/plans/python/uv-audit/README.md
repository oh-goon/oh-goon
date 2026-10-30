# python/uv-audit

Audit the repository-root uv lock with native uv vulnerability audit.

**Status:** runnable with prerequisites. **Effects:** Reads the lock and makes the declared online vulnerability queries.

**Prerequisites:** Native uv supporting `uv --preview audit`, a repository-root uv lock and admitted `host_unrestricted` egress. No dependency group is selected by this Goon.

```sh
oh-goon init --template python/uv-audit --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: python/uv-audit}
target: {repository: /absolute/path/to/project}
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** Native JSON audit output and Step status. Missing uv/network and vulnerability findings retain their actual outcomes.
