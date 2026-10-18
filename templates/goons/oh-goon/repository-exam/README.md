# oh-goon/repository-exam

Validate the version-bound repository-understanding question/answer bank and publish its machine-readable exam manifest as evidence.

This Goon certifies **exam material**, not an LLM. It does not launch a coding agent, grade natural-language answers, or grant repository write authority. Candidate execution remains a separate hard read-only assignment against an answer-free prepared checkout.

The Step runs as verification against the already-initialized repository. Canonical `make init` owns dependency materialization; the Goon resolves the repository-owned `oh-goon-repository-understanding-exam` entrypoint through native `uv run --offline --frozen --no-sync --project backend` rather than binding to an incidental `backend/.venv/bin` path. It has no network capability and does not reacquire packages.

**Artifact retention:** The repository-understanding certification manifest is `evidence`, not a separately retained release package. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
