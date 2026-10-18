# python/release-metadata

**Production template. Production Plan.**

Runs only `python/release-metadata` with explicit `release_channel` and `revision`. The capability reads `project.version` and publishes `project_version` plus the deterministic channel-qualified `release_version`.

No registry, image, chart, secret, or ambient CI value belongs to this Plan.

## Release identifier grammar

`project.version` must be a canonical `MAJOR.MINOR.PATCH` numeric version with no leading-zero components. `revision` must be 7-40 lowercase hexadecimal characters. Alpha/beta identifiers are `<version>-alpha.<revision>` / `<version>-beta.<revision>`; production uses the project version unchanged. No case folding, punctuation cleanup, or ambient Git revision lookup occurs.
