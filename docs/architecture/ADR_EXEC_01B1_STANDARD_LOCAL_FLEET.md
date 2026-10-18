# ADR — Standard-local Fleet Plan through original owners

**Status:** development checkpoint. Execution first; security proportional.

## Problem

The existing actual non-CI/CD Step executor worked but the normal production
`plan run --wait` CLI path required independent high-security Fleet provisioning
unconditionally. Ordinary operator-trusted host scripts could not reach the
existing Fleet admission, SQLite journal and Game Tape despite not selecting
high-security execution. The Git repository subject fence also treated routine
`.git/index.lock` changes as repository replacement, invalidating its own Run.

## Decision

An explicit `--standard-local --wait` opt-in on `plan run` selects an existing
non-CI/CD host-execution mode in the original composition. It is not a sandbox;
the operator consciously authorizes use of their local host/repository. Existing
FleetRun lifecycle, Journal admission, process supervisor, Step evidence and Game
Tape remain authoritative. The normal production build is unchanged: absent the
flag, enrolled authenticated Fleet admission is still required. Standard-local
rejects CI/CD plans and Agent Steps at the original preplanning lifecycle cut;
these cannot silently downgrade into unsandboxed host execution.

The repository subject identity retains root path/device/inode/type/ctime and
Git-marker device/inode/type. Directory marker ctime is omitted since Git status
can create/remove index.lock without Git authority replacement. A Git marker
*file* retains ctime. Source bytes are separately checked by the existing
Hashmarks source snapshot; no second identity backend is added.

Missing pinned Hashmarks is a concrete, actionable dependency refusal. Test
harnesses may simulate that external leaf only, but cannot assert native producer
or Linux/WSL qualification from that substitution.

## Rejected

- An alternate local Plan runner or journal;
- A silent global downgrade for all Fleet Plan execution;
- A new signer, mandatory `fs-verity`, privileged host setup or advanced custody
  for normal trusted maintenance;
- Fake process results, Game Tape data, or a replacement Hashmarks implementation.

## Next

Run real installed Hashmarks on Linux and WSL, then verify original ordinary
FleetRun pass/fail/timeout/cancellation/restart through CLI with actual outputs.
