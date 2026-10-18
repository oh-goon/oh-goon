# python/ruff

Run Ruff formatting and lint checks from the repository's locked `dev` dependency group.

The repository owns its Python dependencies through `pyproject.toml` and `uv.lock`; this Goon does not repeat package names, lock checks, cache wiring, or tool-version evidence.

**Artifact retention:** The code-quality JSON is `evidence`, not an independently reused content artifact. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
