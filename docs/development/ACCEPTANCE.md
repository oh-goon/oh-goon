# Acceptance suites

> **Diagnostic capability reference — not a junior release workflow.** The canonical junior/operator sequence is `oh-goon/app-certification` → APP PASS → `oh-goon/prepare-release` → persisted-artifact certification. The Make targets below are lower-level acceptance capabilities and must not be assembled into a parallel release orchestrator.


Acceptance is external evidence, not an Oh Goon runtime subsystem. Production code must not import or depend on acceptance/certification helpers, special modes, databases, endpoints, or flags.

The following four permanent suites are lower-level acceptance capabilities:

```text
make acceptance-release
make acceptance-container
make acceptance-product
make acceptance-scale
```

## Release acceptance

`acceptance-release` delegates to the locked release acceptance path:

```text
committed backend/uv.lock + committed frontend lock
→ verified wheel build
→ full browser + reviewed visual release matrix
→ export hash-checked runtime requirements directly from committed backend/uv.lock
→ clean temporary install with wheel dependency resolution disabled
→ real Repo/Goon/Game Tape journey
→ production-browser journey
→ restart with persisted state
```

This is release-acceptance evidence used by the owning certification/release flow; by itself it is not APP PASS, package authorization, or persisted-artifact certification. It is intentionally fail-closed when the committed Python dependency authority, committed frontend lock, or exact release toolchain is unavailable. The temporary install is not allowed to create new backend dependency history: runtime requirements are exported from committed `backend/uv.lock`, installed with hash enforcement, and the wheel itself is installed with `--no-deps`.

## Container acceptance

`acceptance-container` runs the same product journey against both supported vendor examples:

```text
Ubuntu
openSUSE
```

Each vendor must consume the same committed `backend/uv.lock` and committed frontend lock directly, then build and verify the wheel payload, install that wheel without dependency re-resolution, persist the complete Oh Goon state root on the host, survive container replacement, and expose the same product semantics. Acceptance also proves the running container imports Oh Goon from the installed wheel. Vendor images are packaging variants, not separate Oh Goon implementations.

## Product acceptance

`acceptance-product` starts the real source runtime and executes one ordinary acceptance repository through the public API:

```text
Repository
→ Goon
→ Step
→ Game Tape
→ production browser
```

It never uses the browser fixture API/page. It does not install dependencies implicitly; run `make init` for native uv/npm project materialization and `make browser-setup` for local browser binaries when they are not prepared.

## Scale/deployment acceptance

`acceptance-scale` aggregates the existing real Scout deployment scenarios:

- guarded real-Uvicorn 5k/500 HTTP workload and zero outbound network attempts;
- real Nginx proxy path;
- process CPU/RSS/storage measurements;
- abrupt SIGKILL/restart persistence without reseeding;
- observer failure isolation from execution;
- corrupt disposable sidecar rebuild from canonical Journal truth;
- Run/Step retention and WAL checkpoint behavior.

The detailed `scout-*-certification` commands remain diagnostic components for isolating failures. They are not separate product features and are intentionally not advertised as the normal acceptance workflow.

## Stopping rule

Add a new acceptance scenario only when it covers a materially different production failure mode. Prefer extending one of the four suites over creating another top-level concept.

Acceptance code may observe and exercise production behavior, but production code must never depend on acceptance machinery.
