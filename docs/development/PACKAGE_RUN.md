# PACKAGE run — junior/operator contract

Do not use this workflow until one uninterrupted complete `oh-goon/app-certification` Plan Run has produced `APP PASS` for the exact source repository authority. The release version must already have been authored with `make release-version-set OH_GOON_RELEASE_VERSION=<x.y.z>` **before** that certification.

`release-version-set` is itself a fail-before-transition owner. It first admits canonical source and validates the incoming native dependency authority, then delegates version authoring to native uv/npm, regenerates the owned generated contracts, and returns success only after the authored authority has converged. If that transition fails after mutation starts, it restores its owned authoring state. Do not precede it with a hand-built chain of architecture/frontend/P0 commands in order to make it safe; those commands remain diagnostics.

Package production has one orchestration owner:

`templates/plans/oh-goon/prepare-release/plan.yml`

Run it through the single Make entrypoint:

```bash
make prepare-release
```

The Make entrypoint derives the current repository identity, verifies canonical APP PASS for that exact admitted source authority, refreshes only the deterministic Plan `goon.lock`, and starts locked release execution. It does not restamp the version and it does not create or clean a replacement source candidate. Do not generate the release Plan lock manually.

The Plan owns exactly one Atomic Goon: package/store. `oh-goon/release-package-store` receives the strict immutable repository workspace for the already-certified source and executes `oh-goon-package-source` through the repository's locked `backend` uv project. Native uv owns project validation, materialization, console-entrypoint resolution, and execution; the Step never inherits or copies the live checkout's `backend/.venv`. Do not call `oh-goon-package-source` directly. The internal byte builder has no operator CLI.

A successful PACKAGE run proves package production only. It does **not** certify the retained artifact.

After packaging, persistent-artifact certification must consume the exact retained ZIP bytes, verify their digest, clean-extract them as a fresh repository instance, and run the same `oh-goon/app-certification` Plan against those bytes. Never reconstruct the ZIP for certification and never create a ZIP-specific APP Plan.

Deep dogfood is optional diagnostic evidence after packaging. When used, invoke the exact-artifact lifecycle helper with both the retained ZIP and the exact `ARCHIVE_SHA256` receipt from package production; deep dogfood refuses a digest mismatch before extraction or execution.

If the release Goon fails, use the emitted `CERTIFICATION PLAN FAILED` block exactly like an APP repair handoff. The evidence command is `make certification-explain OH_GOON_RUN_ID=<fleet-run-id>`.
