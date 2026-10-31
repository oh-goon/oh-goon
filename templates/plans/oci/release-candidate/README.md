# oci/release-candidate

Run Python quality, then build one retained OCI archive.

**Status:** runnable with prerequisites. **Effects:** Runs checks and builds/retains an image workspace.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. A compatible isolated OCI builder, a repository Containerfile with digest-pinned base images, the selected base-pull registry and configured retained workspace storage. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon init --template oci/release-candidate --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: oci/release-candidate}
target: {repository: /absolute/path/to/project}
inputs:
  typechecker: ty
  base_registry_hostname: registry.example.com
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** JUnit evidence and retained OCI build workspace/evidence. No registry publication or credential binding is selected.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
