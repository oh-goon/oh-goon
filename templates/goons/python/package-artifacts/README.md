# python/package-artifacts

Run the repository's `package-artifacts` command from its locked `dev` environment and retain the resulting distribution bundle and manifest.

Packaging and archive verification are project logic, so they belong in a real project CLI rather than an embedded Python program in Goon YAML. The project command is expected to build and validate the wheel/sdist set, then write the declared files.

**Artifact retention:** The distribution bundle uses `content_store` for later retrieval; its manifest uses `evidence` to document what was produced. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
