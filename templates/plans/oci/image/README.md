# oci/image

Build one retained OCI archive through the typed isolated builder.

**Status:** runnable with prerequisites. **Effects:** Builds and retains an image workspace; base pulls use the selected registry.

**Prerequisites:** A compatible isolated OCI builder, a repository Containerfile with digest-pinned base images, the selected base-pull registry and configured retained workspace storage. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init oci/image --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: oci/image}
target: {repository_id: REPOSITORY_ID}
inputs:
  base_registry_hostname: registry.example.com
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** OCI archive `oci/image.oci.tar`, deterministic `oci/image-build.json` evidence and the published workspace. This package has no publication destination or credential binding.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
