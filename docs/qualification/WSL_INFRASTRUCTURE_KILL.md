# Native WSL development qualifier: repeated launcher SIGKILL

Classification: development witness infrastructure diagnostics only. Not Agent execution, Gate A, Gate B, Gate C, APP PASS or release evidence.

## Observed failure (2026-10-13)

Six independently selected B3A rings printed `Killed` before a verified ring receipt. The original qualifier repeatedly invoked `uv run --locked --group dev`, allowed each launcher a 210-second outer budget, and proceeded despite missing supervisor receipts. The console does not establish OOM versus timeout versus external host kill. No host logs were supplied for that incident.

## Revised canonical qualifier

- After exact-source/WSL admission and source PRE, run `uv sync --locked --group dev` **once** with a distinct launch log, bounded deadline, and host memory snapshots.
- Then run each original B11 process supervisor as `uv run --locked --no-sync --group dev python -m ...`; no second test runner, lock owner or unbounded dependency bootstrap.
- Snapshot `/proc/meminfo`, `/proc/pressure/memory`, current/root cgroup memory limits and `memory.events` before and after each ring. Host diagnostics are NOT an LACE physical fact.
- If the launcher is killed (`137`), externally timed out (`124`), receives `143`, or fails to create a process receipt, emit `INFRA_FAILURE.txt`, preserve `RING_RESULTS.txt` with the original exit value and stop. The existing EXIT trap still checks post-source transport hashes.
- Ordinary completed test failures continue to count as failures; this stop rule applies only to launcher infrastructure loss.
- Never set `QUALIFY_ALLOW_WORKING_SOURCE=1` to bypass a package mismatch. Extract a fresh candidate.

## WSL evidence inspection

After a failed run use the printed `EVIDENCE_DIR` (which may be suffixed `-run-001`):

```sh
cat "$EVIDENCE_DIR/INFRA_FAILURE.txt"
cat "$EVIDENCE_DIR/UV_LOCKED_SYNC.log"
cat "$EVIDENCE_DIR/RING_RESULTS.txt"
cat "$EVIDENCE_DIR/UV_SYNC_BEFORE_HOST.txt"
cat "$EVIDENCE_DIR/UV_SYNC_AFTER_HOST.txt"
```

Also inspect `CORE_LAUNCH.log`, `CORE_BEFORE_HOST.txt`, `CORE_AFTER_HOST.txt`, and kernel OOM messages if host permissions allow. Increased cgroup `oom_kill` counters support an OOM diagnosis; no matching counters do not rule out all host-origin kills. A launcher timeout requires investigating the blocked process, not arbitrarily extending the deadline.

Run a new exact-source qualification only from a new directory; never claim success without valid independent process receipts and source POST integrity.
