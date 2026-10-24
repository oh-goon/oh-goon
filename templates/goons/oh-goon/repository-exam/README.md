# oh-goon/repository-exam

Validate the version-bound repository-understanding question/answer bank and publish its machine-readable exam manifest as evidence.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**What to edit / settings owner:** the repository owns this internal package and its native command. Use its owning Make/certification route; do not initialize it as a portable user workflow. `source_workspace` inputs, when declared, are typed earlier-producer references and never host paths.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

This Goon certifies **exam material**, not an LLM. It does not launch a coding agent, grade natural-language answers, or grant repository write authority. Candidate execution remains a separate hard read-only assignment against an answer-free prepared checkout.

The Step runs as verification against the already-initialized repository. Canonical `make init` owns dependency materialization; the Goon resolves the repository-owned `oh-goon-repository-understanding-exam` entrypoint through native `uv run --offline --frozen --no-sync --project backend` rather than binding to an incidental `backend/.venv/bin` path. It has no network capability and does not reacquire packages.

**Artifact retention:** The repository-understanding certification manifest is `evidence`, not a separately retained release package. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
