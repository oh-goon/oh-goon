# oh-goon/template-startability

Run the catalog-wide shipped-template startability proof through the repository-owned certification harness.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**What to edit / settings owner:** the repository owns this internal package and its native command. Use its owning Make/certification route; do not initialize it as a portable user workflow. `source_workspace` inputs, when declared, are typed earlier-producer references and never host paths.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

The harness discovers the shipped Plan catalog through `PlanTemplateStore`, resolves every Plan through the canonical production resolver, binds the smallest declared real repository fixture, proves required native/project execution authority, and drives production admission only through `RUN_ADMITTED`.

It must stop before Run/Step workload execution. This Goon delegates to the focused repository target; `make certify` remains the release-critical authority because the canonical APP certification Plan orders this Goon.
