# python/quality

Compose the small Python formatting, linting, type-checking, and test Goons into one strict quality capability.

This is intentionally composition rather than a large atomic Goon. Each child owns one meaningful capability while the repository's `uv.lock` remains the only Python dependency authority.

## Explicit recipient forwarding

```yaml
- import: python/test
  pass: [coverage_threshold]
```

Only `python/test` receives the public `coverage_threshold` input. This is
identical to an import-local `with: {coverage_threshold: '${{ inputs.coverage_threshold }}'}`.
The child owns all typed constraints and defaults; no value is inherited by
`python/ruff` or `python/typecheck`.
