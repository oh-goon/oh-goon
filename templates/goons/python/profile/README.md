# python/profile

Profile pytest with Pyinstrument from the repository's locked `dev` dependency group and retain the HTML profile as diagnostic evidence.

This Goon deliberately contains no lock-check step, cache warmup step, package list, or generated toolchain manifest; those are execution concerns rather than authoring concerns.

**Artifact retention:** The profiling HTML is `evidence` for inspection; the template does not require durable content-store publication. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
