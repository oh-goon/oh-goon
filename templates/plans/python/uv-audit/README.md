# python/uv-audit

Audit the repository-root uv lock with native uv vulnerability audit.

**Status:** runnable with prerequisites. **Effects:** Reads the lock and makes the declared online vulnerability queries.

**Prerequisites:** Native uv supporting `uv --preview audit`, a repository-root uv lock and admitted `host_unrestricted` egress. No dependency group is selected by this Goon.

```sh
oh-goon templates init python/uv-audit --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/uv-audit}
target: {repository_id: REPOSITORY_ID}
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** Native JSON audit output and Step status. Missing uv/network and vulnerability findings retain their actual outcomes.
