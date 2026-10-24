# oci/release-deploy

Run quality, publish an immutable image, update Kubernetes and verify the exact resulting generation.

**Status:** runnable with prerequisites. **Effects:** Runs checks, builds/publishes an image and mutates/verifies one Deployment.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. A compatible isolated OCI builder, a repository Containerfile with digest-pinned base images, the selected base-pull registry and configured retained workspace storage. Supply `OCI_REGISTRY_USERNAME` and `OCI_REGISTRY_PASSWORD` through their declared host environment bindings for the destination registry. A reachable selected Kubernetes API and authority to change the named Deployment/container. Bind `KUBERNETES_TOKEN` and `KUBERNETES_CA_CERTIFICATE` through the host environment. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init oci/release-deploy --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: oci/release-deploy}
target: {repository_id: REPOSITORY_ID}
inputs:
  typechecker: ty
  base_registry_hostname: registry.example.com
  publish_registry_hostname: harbor.example.com
  publish_repository: team/app
  kubernetes_api_hostname: kube.example.com
  namespace: production
  deployment: api
  container: api
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** The exact publisher `OCI_REFERENCE` feeds `kubernetes/release`; generation/image outputs feed rollout verification. A mutable-tag substitute is not an equivalent input.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
