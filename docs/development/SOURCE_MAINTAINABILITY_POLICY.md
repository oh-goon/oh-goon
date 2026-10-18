# Source maintainability policy

Oh-Goon does **not** own a custom source-size/complexity authority. Physical line counts, function length, branch/nesting counts, formatting shape, `# noqa` usage, and similar quantitative proxies do not determine APP certification, admission, release authority, or architectural correctness.

## Permanent rule

A quantitative source-code proxy must never substitute for a semantic invariant. A product that remains semantically correct, safe, reproducible, and fully functional after harmless source growth or equivalent code movement remains certifiable.

Do not reintroduce:

- hard file/function line ceilings;
- branch or nesting ceilings;
- grandfathered source-shape baselines;
- per-file exceptions to source-shape limits;
- certification failures based on formatting/decomposition shape;
- a second Oh-Goon maintainability scanner when native tooling already owns useful diagnostics.

Use native developer tooling such as Ruff, mypy, pytest and focused architecture/security tests for the concerns they actually own. If a large source owner reveals a real defect, name and test that semantic defect directly—for example cancellation ownership, duplicated authority, unclear responsibility, or an unsafe execution boundary.

Historical phase documents may mention the retired `SOURCE_MAINTAINABILITY_BASELINE.json` and old numeric thresholds. Those are historical evidence only and must not be treated as current policy.

The governing architecture rules are `FAIL_BEFORE_WORK.md` and `ARCHITECTURE_DECISIONS.md`: certification proves product/runtime semantics; source shape is not product authority.
