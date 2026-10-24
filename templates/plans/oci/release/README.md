# oci/release

Run Python quality, then build and publish an immutable OCI image.

**Status:** runnable with prerequisites. **Effects:** Runs checks, builds an image and publishes by digest.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. A compatible isolated OCI builder, a repository Containerfile with digest-pinned base images, the selected base-pull registry and configured retained workspace storage. Supply `OCI_REGISTRY_USERNAME` and `OCI_REGISTRY_PASSWORD` through their declared host environment bindings for the destination registry. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init oci/release --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: oci/release}
target: {repository_id: REPOSITORY_ID}
inputs:
  typechecker: ty
  base_registry_hostname: registry.example.com
  publish_registry_hostname: harbor.example.com
  publish_repository: team/app
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** JUnit evidence plus `OCI_DIGEST` and `OCI_REFERENCE` from publication.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
