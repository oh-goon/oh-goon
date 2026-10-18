# ADR — QENV1 native ring coverage belongs to its actual test owner

Status: implemented development-only, P1B27B2B3B1A, 2026-10-14.

## Evidence and defect

The independently uploaded P1B27B2B2A native witness reported 770 successful
JUnit tests across six rings, but AGENT_PREWORK was REJECTED because its
required-module roster included three native mount tests only executed by
PHYSICAL_OBSERVER. All three were present and successful in that ring.

The supervisor was correct to reject missing required modules. Do NOT weaken
its fail-closed acceptance decision, synthesize a PASS receipt, or transfer
qualification status to any newer source. The witness source snapshot and
pre/post manifest were identical and all 47 recorded sidecar hashes matched.

## Decision

Retain exactly one existing `make qualify-development-wsl` / QENV1 preparation,
one original supervisor and one physical observer ring. Place each B27 native
mount/FD test's `--require-module` in PHYSICAL_OBSERVER, where its *actual test
path* is selected. Keep Agent prework's source-admission modules in
AGENT_PREWORK. The existing QENV1 architecture checker now statically binds
marker, selected test and required-module to the same ring; mutation tests
reject missing tests, moved markers and duplicate/misowned requirements.

This is test qualification routing only: no runtime observer, LACE reporter,
signer, Enforcer admission, AGENT permission or execution path changes.

## Gate boundary

The failed P1B27B2B2A native evidence remains FAIL for that exact source.
The new source must be extracted fresh and run again under native WSL.
Native development PASS is not physical Gate B; physical FD-bind provenance,
independent custody, bounded teardown and absence are still unproven.
