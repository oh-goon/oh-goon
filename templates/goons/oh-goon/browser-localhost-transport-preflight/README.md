# oh-goon/browser-localhost-transport-preflight

Fail closed before browser qualification unless the same Node TCP stack used by Playwright can connect to a live IPv4 localhost endpoint and promptly observe `ECONNREFUSED` after that exact endpoint closes.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**What to edit / settings owner:** the repository owns this internal package and its native command. Use its owning Make/certification route; do not initialize it as a portable user workflow. `source_workspace` inputs, when declared, are typed earlier-producer references and never host paths.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

The probe allocates an OS-selected ephemeral port. It does not hard-code Vite ports, inspect WSL routing internals, or require a particular WSL networking mode. WSL mirrored networking is diagnostic guidance only when the observable localhost transport contract fails.

This proves **Node localhost TCP connect/closed-endpoint refusal**, not browser
engine launch, harness listing or runtime port ownership. The other browser
preflights invoke the transport check through native npm prerequisites, but this
remains an independently identifiable failure boundary. See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
