# Repository lifecycle semantics

Oh Goon keeps two facts separate:

1. **configured League folders** are durable configuration;
2. **current repository catalog entries** are live filesystem discovery.

A configured folder does not become an execution-health record. If the folder cannot currently be accessed as a directory, `/api/locations` keeps the configured path and reports it in `unavailable`. Repositories beneath that folder are absent from the current repository catalog until the folder returns.

This must not be presented as an execution failure. Execution outcomes remain immutable Game Tape/Fleet Run evidence from the time they occurred.

## Disappearance and recovery

The real product acceptance exercises:

```text
configured folder
→ discover repository
→ execute acceptance Goon
→ Game Tape success
→ move configured folder away
→ /api/locations reports unavailable
→ current repository catalog becomes empty
→ prior Game Tape remains readable
→ restore folder
→ unavailable clears
→ repository discovery returns
```

Availability is computed from the filesystem on read. No unavailable flag is persisted and no background repair job rewrites configured paths.

## Repository rename/move

Current repository identity is path-derived. Moving/renaming a repository inside an available configured root therefore produces the current path-derived repository identity during discovery. Historical runs keep their recorded execution identity and are not rewritten to pretend the old execution occurred at the new path.

If real operator usage shows that cross-path identity continuity is required outside historical Scout/Game Tape evidence, that should become a separate product decision rather than hidden rename inference.

## Stopping rule

Do not add a generic repository health score. Report concrete current filesystem availability at the configured-location boundary, and keep execution success/failure in Game Tape.
