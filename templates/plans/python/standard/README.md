# python/standard

**Production template. Production Plan.**

Native oh-goon conversion of the supplied GitLab standard-python intent: explicit release metadata, strict Python quality checks. Docker/registry mutation is deliberately not part of this strict local template.

## Contract

- Canonical identity: `python/standard`.
- Effective execution/resource policy is machine-owned and rendered by Plan inspection; this README does not duplicate it.
- Executable behavior is forbidden here; this file is an ordered Goon roster.
- Inputs: `release_channel`, `revision`, `coverage_threshold`.
- Ordered Goons: `python/release-metadata`, `python/quality`.

## Safety boundary

The Plan contains no executable Steps. It only selects ordered Goons; execution/resource policy is resolved by the machine-owned Plan contract and admitted executor capacity, while implementation, requirements, secrets, network authority, and artifacts stay with the owning Goons.

Machine-readable truth is [`plan.yml`](./plan.yml). The README explains intent and safety only; it is never runtime authority.

## Release identifier grammar

`project.version` must be a canonical `MAJOR.MINOR.PATCH` numeric version with no leading-zero components. `revision` must be 7-40 lowercase hexadecimal characters. Alpha/beta identifiers are `<version>-alpha.<revision>` / `<version>-beta.<revision>`; production uses the project version unchanged. No case folding, punctuation cleanup, or ambient Git revision lookup occurs.
