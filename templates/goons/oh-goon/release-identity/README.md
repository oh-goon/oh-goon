# oh-goon/release-identity

Verify VERSION, backend package identity, frontend package identity and generated OpenAPI release identity all agree.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**Use:** run the repository-owned Make/certification route from the prepared checkout. [`goon.yml`](goon.yml) owns the execution contract; [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) explain captured workspace inputs.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

This is an Oh-Goon repository verification capability. It resolves the repository-owned `oh-goon-release-version` entrypoint through native `uv run --offline --frozen --no-sync --project backend`; the Goon owns orchestration/evidence only and does not bind execution authority to an incidental `backend/.venv/bin` layout or redefine the gate.
