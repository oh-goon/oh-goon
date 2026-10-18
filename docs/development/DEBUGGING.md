# Debugging

## Trace one fleet run

```bash
uv run --offline --frozen --no-sync --project backend oh-goon dev explain-run <fleet-run-id>
```

The trace prints capability resolution, target, lifecycle events, accepted operation IDs, repository verdicts, and current terminal state.

## Common direction

- wrong historical value → inspect `OperationReport` / journal event that created it;
- wrong current UI value → inspect the pinned read view and owning projection;
- run stuck/busy → inspect execution coordinator/lease plus run events;
- frontend request failure → inspect the typed API error code before backend prose.
