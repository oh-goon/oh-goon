# ADR CO-R3C — Trust termination, process quiescence and proof boundaries

**Decision:** Keep the existing `RepositoryEffectExecution` trust-epoch observation, `run_command`/`terminate_owned_process_tree` supervisor and Linux `process_group_exists` as the only original execution/termination chain. Strengthen the Linux observer against partial `/proc` data. **Do not** promote an owned process-group receipt to independent LACE physical absence, or use it to authorize workspace cleanup.

## Original-owner implementation change

`runtime/process_group.py::_linux_process_group_has_live_member` previously allowed one readable `/proc/<pid>/stat` to make an otherwise incomplete inventory look complete. If another PID's `stat` was missing, malformed or denied, the function could return `False` for an unknown group. Now it records partial inventory and returns `None` instead of false absence. The existing `process_group_exists` falls back to `os.killpg(pgid, 0)` and treats EPERM as **present/uncertain**. The backend is not replaced; an incomplete observation fails conservatively. This remains group-level and is **not** an independent inventory or LACE attestation.

## Real hosted process-level evidence

- A leader launches an actively writing descendant in its original process group. The descendant ignores SIGTERM. The original `run_command` authority-termination callback requests `trust_revoke`; the existing TERM/grace/KILL machinery reports group quiescence, and a subsequent physical file observation confirms that the writer has stopped mutating bytes.
- A separate deliberately escaped-session writer is **still observed writing after** the original process group's trust-revocation termination returns quiescent. This is an explicit *negative control*, not a passing full-containment witness. The test always terminates the escaped writer in its `finally` cleanup.
- An unreadable `/proc` entry and a malformed proc record cannot prove absence. Permission-denied kernel probing returns **present/uncertain**, not absent.
- Original X0 architecture mutations prevent dropping the physical writer, escaped survivor, cleanup, conservative /proc scan or original QENV1 CORE enrollment.

The tests invoke the real process supervisor but supply a **test-owned** `TerminationSignal`; they do not simulate authenticated Enforcer trust custody or prove end-to-end trust revocation from an external issuer. The captured file is an observation of effects on this local host only, not an independent physical-authority receipt.

## Release/proof boundary (unchanged)

CO-R2b writer-bearing Git retirement must remain **blocked**, even after the owned process group becomes quiescent. A detached/set-sid, remote, otherwise escaped, or unobservable writer may remain. A qualified independent LACE provider must bind the exact admitted run/workspace/generation and revoke + fence physical writers with negative-evidence admissibility, including unknown/partial states, before any destructive release is allowed. Do not add a secondary cleanup owner or interpret a stored `fence_evidence_json`, settled lease or terminal process status as custody.

CO-R3C real WSL, Bubblewrap native effect, cgroup/descendant containment, trust-epoch revocation from the actual issuer, physical LACE, kernel power loss/ENOSPC/EIO/directory sync, eligible WAL, CO-R2b and release gates remain **unqualified** until separately witnessed. Existing CO-R2c-Q1 development WSL PASS does not transfer to this source.
