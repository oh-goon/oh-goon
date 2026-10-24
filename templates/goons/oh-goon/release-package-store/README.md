# oh-goon/release-package-store

Build the deterministic slim source ZIP directly from the strict immutable repository workspace admitted for the release Plan and retain the exact ZIP in Oh-Goon's content store.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**Use:** run the repository-owned Make/certification route from the prepared checkout. [`goon.yml`](goon.yml) owns the execution contract; [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md) explain captured workspace inputs.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

The source was already version-stamped and APP-certified before this Plan starts. This Goon must not stamp, clean, normalize, or otherwise mutate it. Its only job is to package that admitted authority.

The artifact's Game Tape evidence records its content-store reference, SHA-256 and byte count. The SHA is also published as a normal Goon output for the controller/promotion boundary.

This Goon does **not** certify the archive it just produced. Certification starts only after the retained ZIP is re-materialized as a fresh repository candidate, so producer workspace state can never substitute for persistent-artifact verification.

**Artifact retention:** The immutable source ZIP uses `content_store` for independent readback; checksum and source manifest are `evidence`, and retained bytes must be reverified before certification. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
