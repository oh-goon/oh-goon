# P1B27B2B1 — Original atomic runtime FD kernel custody

**Status:** Development-only prelaunch custody. **Not** kernel mount attestation, B27 physical convergence, Agent effects, or Gate B.

## Context

P1B27B2A signed the predictable native mount source/target/mode roster into the original `RESOURCE_INTENT`. P1B27B1 checked complete Bubblewrap argv against the prepared sources. The atomic executable/interpreter files already used Linux sealed memfds and `--ro-bind-data`, but the handoff checked only descriptor **numbers** and original seals at creation. A changed descriptor number mapping could point at another memfd, even one with matching bytes and seals.

## Decision

Extend the original `SealedExecutionBytes` -> `AtomicRuntimeFile` -> `AtomicRuntimeAuthorityLease` handoff. At the exact moment bytes are sealed, capture their kernel `(st_dev, st_ino)`. The existing lease checks this captured identity with `fstat`, regular-file type, byte size, mode, all four mandatory seals (`WRITE/GROW/SHRINK/SEAL`), complete SHA-256 using `pread`, and the original executable/interpreter receipt binding. Reject reused FD integers and closed leases. Revalidate on entering the existing Bubblewrap lease, when rewinding `--ro-bind-data` descriptors, and immediately before original `run_command`. Neither launch nor journal may invent a new FD owner.

No second mount builder, engine, journal, signer, observer or recovery path is introduced. The original QENV1 qualifier runs the new tests in the existing `AGENT_PREWORK` and `PHYSICAL_OBSERVER` rings. The original X0 architecture gate enforces the handoff order and required checks. Mutation tests deliberately replace an original FD with a different equally sealed memfd holding identical bytes; it MUST fail despite matching content hashes and seals.

## Residual threats and mandatory follow-on

- This work proves **local original sealed FD object continuity immediately before launch**, not which object Bubblewrap or the kernel eventually mounted. Native WSL physical observers must independently confirm mount/FD/namespace ownership and later absence.
- Non-memfd native bind sources (runtime root, writable attempt paths, staged Agent source and host-tool files) are still referenced by host pathnames. P1/B27B2B2 must pin these sources through an actual kernel mount operation or fail closed. Do not interpret `fstat` followed by pathname `--ro-bind` as full TOCTOU closure.
- Dynamically created cgroup/netns and runtime mount namespaces require the original resource envelope, LACE ownership, restart recovery and independently observed post-teardown absence. Retain `UNKNOWN -> fail closed`.
- Agent source remains `effect_permission=NONE`; original `RESOURCE_INTENT` adoption, AG-03/04 model/network/MCP egress controls and Gate A/B/C remain independent blockers.

**Invariant:** Resolve once, transport once, execute once, report once. One original execution authority; one authentic physical observation path.
