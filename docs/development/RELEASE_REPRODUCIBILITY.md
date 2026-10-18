# Release reproducibility

This is a dependency/reproducibility reference, **not a release entrypoint**. The canonical release frontdoor remains `make prepare-release`.

Python dependency intent is authored in `backend/pyproject.toml`; native uv is the sole resolver and `backend/uv.lock` is the exact committed resolution authority. `make sync` is the only normal dependency-authoring path: before it publishes new lock bytes it asks native uv to create an ephemeral venv and perform a dependency-free editable install of the backend with uv-only sources disabled. That proves the same Hatch editable-package metadata/hook mode consumed by normal project materialization without invoking the release-wheel lifecycle or mutating the canonical project environment. Native `uv lock` then authors the lock and native package managers materialize the authored locks.

Consumption is strict. Wherever the full backend project metadata is present and uv may materialize or reconcile the project environment, Oh-Goon uses native `--locked`, not `--frozen`, so a stale `pyproject.toml`/`uv.lock` pair fails instead of silently consuming old resolution. Repeated execution after an already-proven `make init` may use `uv run --offline --frozen --no-sync` because that path is intentionally consumption-only and cannot repair the environment. `make backend-lock-check` is the non-authoring native freshness proof (`uv lock --check`) followed by the small Oh-Goon publication policy. If native uv needs unavailable registry or release-artifact transport to prove freshness, qualification is incomplete until that same native operation can run; Oh-Goon does not implement a second resolver or an offline imitation.

Strict Fleet execution uses the same ownership model for a fresh isolated repository
subject. Admission proves the Oh-Goon-owned structural prerequisites without asking uv to
resolve the same environment against disposable state. Strict preparation is the first
native dependency operation and acquires the locked environment through native
`uv sync --locked --no-default-groups --group <selected>` into one retained run-owned cache outside canonical source. Each physical
attempt performs only offline native materialization into an attempt-owned environment outside
canonical source and then
runs the project command with `uv run --frozen --no-default-groups --no-sync`. A live checkout `.venv` is
never copied or inherited. The release package/store Goon uses this generic path with
`project: backend`, `group: build`, and `uv run oh-goon-package-source`.

The Hashmarks release wheel is intentionally declared as a standards-based PEP 508 direct reference in `project.dependencies`. Do not move release dependency identity into `tool.uv.sources`: uv documents those sources as uv-only development metadata, while Oh-Goon release dependency metadata must remain visible to standards-compliant build/install tooling. Hatch is configured to admit the direct reference because it is deliberate published metadata, not a local development shortcut.

Release-wheel construction separately uses `uv build --no-sources` after the release owner has produced `generated/release/static`. This ensures an accidental future `tool.uv.sources` entry cannot become hidden build authority while preserving the stricter rule that a standard wheel cannot be built before the frontend release payload exists. The committed uv lock must use repository-approved public registries or public GitHub release artifacts; repository-local/path/vendor dependency sources are rejected by the Oh-Goon publication policy.

Release assembly has one pre-materialization dependency-admissibility owner: `make release-locks-check`. It asks native uv and npm to validate their committed locks once, then the release path transports that decision into locked materialization and build. `accept-release` does not re-probe the same locks, and release-version identity checking does not independently re-run package-manager compatibility checks.

Release version stamping remains a deliberately narrower operation than dependency qualification. The version owner uses `uv version --frozen`; it must not run `uv lock --check`, resolve dependencies, or materialize an environment. The already-qualified `backend/uv.lock` must remain byte-identical across stamping. Lock freshness belongs to the earlier native qualification boundary, not to version mutation.

Frontend dependency authoring remains native npm lock authoring. There is no Oh-Goon package resolver, dependency mirror, reconstructed lock, uv-lock normalizer, retained dependency wheel authority, or hidden release source table.
