# kubernetes/set-image

Commit a digest-qualified image to one exact Kubernetes Deployment container.

**Status:** runnable with prerequisites. **Effects:** Mutates one Deployment pod-template image using optimistic JSON Patch tests.

**Prerequisites:** A reachable selected Kubernetes API and authority to change the named Deployment/container. Bind `KUBERNETES_TOKEN` and `KUBERNETES_CA_CERTIFICATE` through the host environment. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon init --template kubernetes/set-image --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: kubernetes/set-image}
target: {repository: /absolute/path/to/project}
inputs:
  kubernetes_api_hostname: kube.example.com
  namespace: production
  deployment: api
  container: api
  image: registry.example.com/team/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `KUBERNETES_RESOURCE_VERSION`, `KUBERNETES_GENERATION` and `KUBERNETES_IMAGE`. This Plan records the mutation and does not claim rollout readiness.
