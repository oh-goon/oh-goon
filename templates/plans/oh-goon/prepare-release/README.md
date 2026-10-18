# oh-goon/prepare-release

This Plan is the **single orchestration owner** for release-package production. It has one responsibility: `oh-goon/release-package-store` packages the exact source authority that already produced canonical APP PASS and retains that exact ZIP in the strict content store.

## Use this Plan

```sh
oh-goon templates show oh-goon/prepare-release --kind plan
oh-goon plan inputs templates/plans/oh-goon/prepare-release/plan.yml
```

Copy with `oh-goon templates show oh-goon/prepare-release --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

Release version authoring is deliberately **not** part of this Plan. `make release-version-set OH_GOON_RELEASE_VERSION=<x.y.z>` is the explicit pre-certification authoring operation. Once APP certification passes, release preparation must not stamp, clean, normalize, or otherwise create a new source candidate. It may only validate and package the already-certified authority.

`oh-goon-package-source` owns publication of `source.zip`, its SHA sidecar and manifest. `oh_goon.repository_tooling.release.package_slim` is only the internal deterministic byte builder used by that publication script and is not a second release entrypoint.

The retained ZIP is not promoted merely because this Plan passed. Persistent-artifact certification must consume the **exact retained bytes**, verify their digest, clean-extract them as a fresh repository instance, and run the **same `oh-goon/app-certification` Plan** against that extracted candidate. It must never reconstruct a replacement ZIP and it must never introduce a ZIP-specific copy of the APP test policy.

## Validate your copy

```sh
oh-goon plan validate plan.yml
oh-goon lock plan.yml
```
