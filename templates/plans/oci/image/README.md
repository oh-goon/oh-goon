# oci/image

Build one retained OCI archive through the typed isolated builder.

**Status:** runnable with prerequisites. **Effects:** Builds and retains an image workspace; base pulls use the selected registry.

**Prerequisites:** An admitted strict executor with Buildah and crun, a repository Containerfile with digest-pinned base images, the selected base-pull registry and configured retained workspace storage. The current local backend refuses strict Runs until independent native parent custody can be established; installing these tools alone does not qualify it. See the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon init --template oci/image --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: oci/image}
target: {repository: /absolute/path/to/project}
inputs:
  base_registry_hostname: registry.example.com
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** OCI archive `oci/image.oci.tar`, deterministic `oci/image-build.json` evidence and the published workspace. This package has no publication destination or credential binding.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
