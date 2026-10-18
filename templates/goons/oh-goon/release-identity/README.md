# oh-goon/release-identity

Verify VERSION, backend package identity, frontend package identity and generated OpenAPI release identity all agree.

This is an Oh-Goon repository verification capability. It resolves the repository-owned `oh-goon-release-version` entrypoint through native `uv run --offline --frozen --no-sync --project backend`; the Goon owns orchestration/evidence only and does not bind execution authority to an incidental `backend/.venv/bin` layout or redefine the gate.
