# python/dependency-inventory

Observe repository dependency manifests without installing dependencies.

**Status:** runnable with prerequisites. **Effects:** Reads supported manifests and records dependency evidence.

**Prerequisites:** An explicitly registered repository containing the manifests to observe. This Goon uses `dependency_inventory`; it needs no Python dependency-group installation.

```sh
oh-goon init --template python/dependency-inventory --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: python/dependency-inventory}
target: {repository: /absolute/path/to/project}
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** Repository dependency inventory evidence. Observation does not authorize maintenance or start another Run.
