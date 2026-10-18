# python/release-candidate

**Production template. Production Plan.**

Quality-gated release candidate: compute explicit release metadata, run the strict quality pipeline, then build and verify Python distribution artifacts. No OCI image or registry mutation is implied by this plan.

## Contract

- Canonical identity: `python/release-candidate`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `release_channel`, `revision`, `coverage_threshold`.
- Ordered Goons: `python/release-metadata`, `python/quality`, `python/package-artifacts`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.

## Release identifier grammar

`project.version` must be a canonical `MAJOR.MINOR.PATCH` numeric version with no leading-zero components. `revision` must be 7-40 lowercase hexadecimal characters. Alpha/beta identifiers are `<version>-alpha.<revision>` / `<version>-beta.<revision>`; production uses the project version unchanged. No case folding, punctuation cleanup, or ambient Git revision lookup occurs.
