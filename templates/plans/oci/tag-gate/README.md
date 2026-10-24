# oci/tag-gate

Assert that an existing registry tag resolves to the required digest.

**Status:** runnable with prerequisites. **Effects:** Reads/asserts registry state.

**Prerequisites:** Read access to the selected registry/repository/tag. Supply `OCI_REGISTRY_USERNAME` and `OCI_REGISTRY_PASSWORD` through the declared host bindings. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init oci/tag-gate --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: oci/tag-gate}
target: {repository_id: REPOSITORY_ID}
inputs:
  registry_hostname: registry.example.com
  repository: team/app
  tag: candidate
  expected_digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `OCI_TAG_DIGEST` and `OCI_TAG_RESOLVED_REFERENCE`. A mismatched digest fails the assertion; the workflow does not promote or change tags.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
