# One prepared environment for native development qualification (QENV1)

**Architectural invariant:** Prepare once, verify once, execute many isolated test rings. Dependency environment management must never occur implicitly inside a ring.

`make qualify-development-wsl` remains the **only** front door. It cleans disposable Windows transport sidecars, authenticates the exact packaged source, allocates a fresh external evidence directory, then uses the existing supervised WSL qualifier.

1. **PREPARE:** exactly one `uv sync --locked --group dev` in `backend/`, bounded and recorded as `UV_LOCKED_SYNC.log`. Failure stops all rings.
2. **VERIFY:** exactly one non-mutating, offline `uv sync --check --locked --offline --group dev` in that same project; verify the actual `.venv` Python interpreter and run the existing repository architecture gate. A failure stops all rings. Save `UV_ENV_VERIFY.log`, `QUALIFIER_ARCHITECTURE.log` and `UV_ENV_VERIFIED.txt` containing interpreter and lock/project fingerprints.
3. **EXECUTE:** each ring starts its own supervised process group using `backend/.venv/bin/python` **directly**. No per-ring `uv run`, sync, installation, project resolution, fallback interpreter, or environment patch. `UV_NO_SYNC=1`, `UV_OFFLINE=1`, `PIP_NO_INDEX=1`, `PYTHONNOUSERSITE=1` are defense in depth, not a security boundary against malicious tests. Keep exact per-ring evidence and fail fast on infrastructure loss.
4. **CLOSE:** original PRE/POST source audit and receipts still run on any exit. Source and lock identities cannot be silently changed by a ring.

The original `check_architecture` registration owns an anti-drift guard for the qualification shell, its `make` entrypoint and the verified environment receipt. The guard checks structural phase ordering, one prepare/check command, and the complete absence of dependency setup in the ring body. Its hostile mutations are covered by a focused test; do **not** bypass it with a second qualifier implementation.

Re-running qualification on an exact candidate is allowed to use uv's **download cache**, but a new qualification session must re-establish its locked environment authority. Source-exact qualification must never be replaced with `QUALIFY_ALLOW_WORKING_SOURCE=1` for a packaged candidate.

Development witness only: neither green rings nor the prepared environment prove original issuer/root custody, physical LACE quiescence, Gate A/B/C, APP PASS or release authority.
