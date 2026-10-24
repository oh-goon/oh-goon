# oci/tag-gate

Assert that an existing registry tag resolves to the required digest.

**Status:** runnable with prerequisites. **Effects:** Reads/asserts registry state.

**Prerequisites:** Read access to the selected registry/repository/tag. Supply `OCI_REGISTRY_USERNAME` and `OCI_REGISTRY_PASSWORD` through the declared host bindings. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

**What to edit:** initialize a workflow, then set the registered target and relevant caller choices in `workflow/run.yml`. The illustrative addresses, revision and digest below must identify your real workload before execution. The [first-run guide](../../../README.md) explains repository registration and prepared CLI use.

**Settings owner:** [`plan.yml`](plan.yml) owns ordered imports; imported Goons own input types/defaults, Steps, effects and outputs. `run.yml` selects values. Native project tools keep their own configuration.

```sh
oh-goon templates init oci/tag-gate --output ./workflow
```

Edit the generated `workflow/run.yml`; this shows one explicit selection:

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

**Failures:** invalid inputs fail before work. Inspection/validation do not prove installed tools, reachable services or successful effects. Native failures remain failed Steps with their exit status and logs. A changed source or execution selection requires a new lock.

**Customize:** change caller values in `run.yml`. For structural changes, create a project Plan with ordinary ordered `goons:` imports and select it with `source: {kind: file-plan, path: plan.yml}`; see [Plan authoring](../../README.md). Keep secrets in declared execution environment bindings.

See the [OCI input/registry audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md) for typed workspace and digest lineage.
