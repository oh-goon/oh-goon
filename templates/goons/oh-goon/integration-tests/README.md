# oh-goon/integration-tests

Run the native `make test-integration` suite against the **admitted repository**.
There is no `source_workspace` producer requirement for this standalone internal
verification capability. The repository's Make target owns the integration test
selection and pytest execution; this Goon owns ordering and evidence only.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**Use:** run the repository-owned Make/certification route from the prepared checkout. [`goon.yml`](goon.yml) owns the execution contract; [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) explain captured workspace inputs.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

`integration-contract` delegates the same command but consumes an **explicitly
published earlier-Step workspace**. The different source authority makes the
results semantically different even though argv is identical; do not merge them.

See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
