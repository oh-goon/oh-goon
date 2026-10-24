# oh-goon/integration-contract

Run the existing repository `make test-integration` suite against a **producer-published
source workspace**. The required `source_workspace` input identifies the earlier Step
whose workspace must be explicitly transported to this consumer; it is not an
implicit repository checkout. Missing or invalid producer binding must fail before
Step work.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**Use:** run the repository-owned Make/certification route from the prepared checkout. [`goon.yml`](goon.yml) owns the execution contract; [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) explain captured workspace inputs.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

The suite itself is **not** a separate generated-API-only test implementation.
`integration-tests` invokes the same native Make target, but uses the admitted
repository workspace by default. These two capabilities are distinct because the
source provenance and resulting evidence differ, not because the command differs.

See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
