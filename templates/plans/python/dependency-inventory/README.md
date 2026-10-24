# python/dependency-inventory

Observe repository dependency manifests without installing dependencies.

**Status:** runnable with prerequisites. **Effects:** Reads supported manifests and records dependency evidence.

**Prerequisites:** An explicitly registered repository containing the manifests to observe. This Goon uses `dependency_inventory`; it needs no Python dependency-group installation.

```sh
oh-goon templates init python/dependency-inventory --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: python/dependency-inventory}
target: {repository_id: REPOSITORY_ID}
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** Repository dependency inventory evidence. Observation does not authorize maintenance or start another Run.
