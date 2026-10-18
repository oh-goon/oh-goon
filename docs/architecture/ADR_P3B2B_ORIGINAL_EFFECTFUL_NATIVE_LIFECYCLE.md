# ADR — P3B2B original effectful native lifecycle witness

## Decision

The existing opt-in physical-observer ring runs the original Bubblewrap FD-bind mechanical witness for four supervised child-lifecycle transitions: cancellation, trust revocation, watchdog deadline and a leader that leaves a descendant. All cases use ONE test fixture that delegates to the unchanged original `capture_runtime_root`, `OriginalBindSourceLease`, `BubblewrapProcessIsolation._command`, native FD handoff and `run_command` supervisor. The P3 cases declare `sleep` as a frozen host tool, not a mutable PATH dependency or a new shell launcher.

Each case first verifies actual mounted content, original pinned-FD device/inode, mount IDs and executable child readiness. A missing kernel capability or wrong mount is a failure; no fake backend, skip, or relaxed native requirement is allowed. The original process supervisor must provide a final quiescent process-group receipt. The development test observes this receipt, without converting it to an Enforcer or LACE verdict.

## Authority and limits

This is a DEVELOPMENT-only mechanical witness, not a production Run admission. It does not prove independently authenticated observer custody, detached/foreign namespace holders, cgroup/network resource removal, physical absence, Gate B, or release. The one original Fleet journal and its previous P3A/P3B1/P3B2A unqualified observation semantics are unchanged. The test shares one native launcher construction; it introduces neither runtime entrypoint nor parallel execution/observer/fact publisher. Native WSL qualification remains an independently required acceptance test on the exact candidate bytes.

## Regressions

Existing X0 and QENV1 require shared original authority, four supervised lifecycle transitions, non-skipped bwrap, source manifest identity and exact PHYSICAL_OBSERVER enrollment. Hosted mutation tests deliberately delete original authorization, child readiness, mount comparison and supervisor checks. Only the effectful WSL run can demonstrate the four native scenarios; no hosted substitute counts as proof.
