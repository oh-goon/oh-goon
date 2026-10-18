# EXEC-01A — real standard Atomic Goon process slice

Status: Development evidence; not a complete EXEC-01/Fleet E2E qualification.

## Context and decision

The canonical authoring resolver defaults an omitted `execution` block to CI/CD. That mode **must** use its enrolled backend/isolation placement; a shell process on the host cannot silently impersonate it. A trusted operator-owned Atomic Goon can instead explicitly set `execution.purpose: maintenance` and use the original direct Step execution path. Direct is **not a sandbox** and must not be selected for untrusted code or used to claim enforcement of denied network effects.

This checkpoint adds one standalone example with a generated authoritative `goon.lock` and a *real*, unmocked Linux/WSL integration test. The test goes through the original FleetPlanResolver, locked target preparation (`prepare_execution_target`), FleetPlanRunExecutor.prepare_direct_run, execute_direct_run, the original Step sequence and process supervisor. It checks real stdout evidence, failed exit propagation, process cancellation, actual supervisor deadline and lock drift refusal. No new executor, admission owner, signer, journal, or backend interface.

A `goon.lock` locks the resolved authored Goon graph. It does not make unpinned host shell scripts immutable. The example is therefore trusted/operator-owned source and cannot be represented as hostile-source isolation. A shell-only example does not have a Python project or uv.lock dependency; when executing a Python project, its authoritative uv.lock remains required.

## What EXEC-01A does NOT prove

- No FleetRun admission/journal transaction was executed in this integration test. In particular, direct preflight by itself does not prove an authenticated Enforcer Run admission.
- No durable Game Tape was published by this test. Step attempts and process lifecycle observations are real but in-memory.
- Not a full native WSL host matrix, APP PASS, release, Composite Goon, or Agent Skill execution.
- No entitlement to run untrusted code or a claim that host `egress: none` is physically isolated.

## Next real product boundary

EXEC-01B: connect the ordinary supported workload to **existing** FleetRun admission, original Enforcer authority and Game Tape publication, preserving honest direct-vs-isolated backend claims. Do not add advanced custody or security certification requirements for ordinary workloads. Then verify on Linux and WSL with real source, result, cancellation and durable output.
