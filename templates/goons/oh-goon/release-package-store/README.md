# oh-goon/release-package-store

Build the deterministic slim source ZIP directly from the strict immutable repository workspace admitted for the release Plan and retain the exact ZIP in Oh-Goon's content store.

## Use this Goon

```sh
oh-goon templates show oh-goon/release-package-store --kind goon
oh-goon plan inputs templates/goons/oh-goon/release-package-store/goon.yml
```

Use `goons: [oh-goon/release-package-store]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

The source was already version-stamped and APP-certified before this Plan starts. This Goon must not stamp, clean, normalize, or otherwise mutate it. Its only job is to package that admitted authority.

The artifact's Game Tape evidence records its content-store reference, SHA-256 and byte count. The SHA is also published as a normal Goon output for the controller/promotion boundary.

This Goon does **not** certify the archive it just produced. Certification starts only after the retained ZIP is re-materialized as a fresh repository candidate, so producer workspace state can never substitute for persistent-artifact verification.

**Artifact retention:** The immutable source ZIP uses `content_store` for independent readback; checksum and source manifest are `evidence`, and retained bytes must be reverified before certification. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
