# python/profile

Profile the project’s real pytest workload with Pyinstrument.

**Status:** runnable with prerequisites. **Effects:** Executes tests and writes an HTML profile.

**Prerequisites:** A committed Python project and uv lock; its selected group must supply pytest and Pyinstrument.

```sh
oh-goon templates init python/profile --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/profile}
target: {repository_id: REPOSITORY_ID}
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `pytest-profile.html` is evidence. The project’s native pytest configuration owns discovery.
