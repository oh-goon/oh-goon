# oh-goon/repository-exam

Validate the version-bound repository-understanding question/answer bank and publish its machine-readable exam manifest as evidence.

## Use this Goon

```sh
oh-goon templates show oh-goon/repository-exam --kind goon
oh-goon plan inputs templates/goons/oh-goon/repository-exam/goon.yml
```

Use `goons: [oh-goon/repository-exam]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This Goon certifies **exam material**, not an LLM. It does not launch a coding agent, grade natural-language answers, or grant repository write authority. Candidate execution remains a separate hard read-only assignment against an answer-free prepared checkout.

The Step runs as verification against the already-initialized repository. Canonical `make init` owns dependency materialization; the Goon resolves the repository-owned `oh-goon-repository-understanding-exam` entrypoint through native `uv run --offline --frozen --no-sync --project backend` rather than binding to an incidental `backend/.venv/bin` path. It has no network capability and does not reacquire packages.

**Artifact retention:** The repository-understanding certification manifest is `evidence`, not a separately retained release package. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
