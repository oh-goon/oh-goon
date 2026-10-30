# Execution supervisor convergence

Oh-Goon owns process lifecycle and timeout semantics for both product commands and repository qualification batches.

## Common command owner

`oh_goon.engine.command.run_command` is the common process owner. It owns process-group creation, output draining, cancellation, timeout cleanup, and non-exportable control environment.

Qualification batches use strict process-group quiescence. A pytest leader exiting is not enough to finish a batch when descendants remain alive in the same owned group. The hard batch timeout therefore remains active until the whole group is gone. On timeout, Oh-Goon terminates the group and reports exit 124 with bounded captured output.

This preserves the stronger certification firewall while removing the second `Popen`/kill-tree implementation that previously lived in `repository_tooling.test_batches`.

## Timeout shard refinement

A multi-member pytest batch timeout is execution evidence that the initial shard may be too coarse; it is not automatically evidence that any member fails.

The batch runner therefore bisects timed-out multi-member batches deterministically under the original suite deadline. Each child keeps the same hard per-batch timeout ceiling. Bisection continues until children pass or a single-member shard times out. Single-member timeout remains a failure and is never hidden by further splitting.

This changes scheduling granularity only. It does not weaken assertions, deselection rules, coverage, markers, or suite deadlines.

## Environment authority

The generic command owner strips presentation/control descriptors such as `OH_GOON_PROGRESS_FD` and local access tokens from children. Repository tests cannot bypass pytest capture by inheriting Oh-Goon presentation authority.

An explicitly budgeted waited CLI Run uses this same command owner to launch
the original CLI in one native systemd scope before preparation. Its bounded,
sealed handoff transports the captured configuration and resolved database,
League and SQLite settings; ordinary Step environments keep their existing
sanitization. The child verifies the handoff against its actual native unit
before restoring execution authority. The existing Fleet and Step owners
still execute and report the Run exactly once.

The outer CLI forwards SIGINT/SIGTERM through a birth-matched pidfd to that
original child. Native scope accounting and cleanup observations remain
separate from the durable workload outcome. The parent never rebuilds a global
Journal view to append final accounting: it consumes a bounded child-issued
context and the original Journal owner performs an exact-head append. Changed
or missing authority leaves persistence incomplete. See
[local Run budgets](TEMPLATE_INPUT_INSPECTION.md#limit-a-local-run).

## SQLite preparation observation

Long preparation uses fresh, read-only admitted SQLite handles at bounded observation cadence. If SQLite reports `SQLITE_NOTADB` or `SQLITE_CORRUPT` on one fresh observer open while path identity is unchanged, Oh-Goon performs at most three observation-only re-proof attempts with bounded delays. A successful later proof must re-establish schema, journal, database identity, durable revision, and `PRAGMA quick_check` before execution continues.

No re-proof path creates, repairs, rewrites, resets, or replays durable state. Persistent identity failure still raises `DatabasePathIdentityLost` and stops execution.
