# oh-goon/prepare-release

This Plan is the **single orchestration owner** for release-package production. It has one responsibility: `oh-goon/release-package-store` packages the exact source authority that already produced canonical APP PASS and retains that exact ZIP in the strict content store.

Release version authoring is deliberately **not** part of this Plan. `make release-version-set OH_GOON_RELEASE_VERSION=<x.y.z>` is the explicit pre-certification authoring operation. Once APP certification passes, release preparation must not stamp, clean, normalize, or otherwise create a new source candidate. It may only validate and package the already-certified authority.

`oh-goon-package-source` owns publication of `source.zip`, its SHA sidecar and manifest. `oh_goon.repository_tooling.release.package_slim` is only the internal deterministic byte builder used by that publication script and is not a second release entrypoint.

The retained ZIP is not promoted merely because this Plan passed. Persistent-artifact certification must consume the **exact retained bytes**, verify their digest, clean-extract them as a fresh repository instance, and run the **same `oh-goon/app-certification` Plan** against that extracted candidate. It must never reconstruct a replacement ZIP and it must never introduce a ZIP-specific copy of the APP test policy.
