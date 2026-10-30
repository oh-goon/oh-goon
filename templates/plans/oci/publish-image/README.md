# oci/publish-image

Build and publish an immutable OCI image reference.

**Status:** runnable with prerequisites. **Effects:** Builds an image and publishes by digest to the selected destination.

**Prerequisites:** A compatible isolated OCI builder, a repository Containerfile with digest-pinned base images, the selected base-pull registry and configured retained workspace storage. Supply `OCI_REGISTRY_USERNAME` and `OCI_REGISTRY_PASSWORD` through their declared host environment bindings for the destination registry. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon init --template oci/publish-image --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: oci/publish-image}
target: {repository: /absolute/path/to/project}
inputs:
  base_registry_hostname: registry.example.com
  publish_registry_hostname: harbor.example.com
  publish_repository: team/app
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `OCI_DIGEST` and `OCI_REFERENCE` from the publisher. The base-pull registry and destination registry are independent choices; no mutable tag is created.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
