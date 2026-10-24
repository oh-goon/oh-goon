# oh-goon/backend-p0

Run the canonical release-critical execution/security P0 closeout suite, including generated-contract verification, without substituting a second certification implementation.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**Use:** run the repository-owned Make/certification route from the prepared checkout. [`goon.yml`](goon.yml) owns the execution contract; [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) explain captured workspace inputs.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

This is an Oh-Goon repository verification capability. `make verify-p0` owns the selected test/generation semantics; this Goon owns the exact host-tool authority and orchestration/evidence boundary used when that command runs as an APP verification Goon.

The selected P0 tests construct strict nested Fleet executions. Their shared test fixture consumes this Goon's admitted `git`, `ldd`, `sh`, and `uv` authority; it does not inherit the broader diagnostic `oh-goon/backend-tests` tool set. Plan-specific test harnesses derive their own exact toolchain from the resolved inner Plan instead of reusing the P0 fixture authority.

This keeps the release-critical boundary least-privilege: `node`/`npm` remain absent from P0 unless a P0 test really requires them, while non-P0 tests that resolve Plans containing those tools receive plan-scoped test authority.

Shell authority (`sh`): Nested P0 Fleet fixtures require admitted sh tool authority, independent of the direct make argv.
