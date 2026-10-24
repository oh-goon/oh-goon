# oh-goon/browser-engine-preflight

Launch the configured Playwright browser engines and report missing host dependencies before browser certification.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**Use:** run the repository-owned Make/certification route from the prepared checkout. [`goon.yml`](goon.yml) owns the execution contract; [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) explain captured workspace inputs.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

This is an Oh-Goon repository verification capability. It delegates to the repository's existing canonical command; the Goon owns orchestration/evidence only and does not redefine the gate.

This checks **engine launch and host libraries**, not harness discovery, clean
runtime port ownership or the basic Node localhost TCP boundary. All four
preflights retain distinct result/evidence identities. The native npm target
owns its transport prerequisite. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
