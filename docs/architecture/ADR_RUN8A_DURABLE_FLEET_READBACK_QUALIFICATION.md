# ADR — RUN-8A: Durable Fleet Run readback without false native qualification

## Problem
RUN-1 through RUN-7 validated CLI and service contracts individually, often with
stub Fleet owners. RUN-8 requires the real authenticated Plan → Enforcer → LACE
lifecycle on an independently qualified host; that host is not available here.
A green mocked test cannot substitute for physical containment or exact-source
native authority.

## Decision
Introduce a **test-only integration qualification** that uses OH-Goon's
existing `fleet_stack`, actual `FleetRunControlPlane`, `FleetRunLifecycle`,
`FleetRunExecution`, actual SQLite and Journal, and the original CLI readback.
The operation workload uses a local **test-only** capability runner; this test
does **not** execute a Plan or claim a physical sandbox. The test covers both
successful and failed operations, shuts down the original worker, and creates
a fresh SQLite/Journal/FleetRunControlPlane read owner. From that fresh owner,
RUN-2..6 status/handoff/tape/steps/outputs/logs and RUN-3 cursor resumption
are exercised against actual persisted Run events.

## Ownership
- Fleet lifecycle and journal are real canonical owners; the test creates no
  parallel journal reader, failure classifier, signer, mutation owner or source
  identity implementation.
- Test-only runner synthesizes operation results. It is **not** evidence that
  production effect execution or kernel custody succeeded.
- No Hashmarks package is imported or newly required by this test. The
  production Plan source-identity path still uses the pre-existing Hashmarks
  dependency; no homemade Merkle digest is substituted for it.
- The authoritative `backend/pyproject.toml` and `backend/uv.lock` are unchanged.

## Acceptance and remaining external gate
A PASS proves the original non-Plan Fleet durable lifecycle and agent readback
work together with a fresh process-style reader and bounded cursor continuation.
RUN-8 **remains OPEN** for authenticated Plan admission and true Enforcer/LACE
execution on an independently provisioned host. A read-only cgroup-v2 mount or
missing required native authority means BLOCKED, not a test PASS. Independent
Gate A/B/C, APP PASS and release remain blocked.
