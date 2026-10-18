# ADR — Execution first; security proportional to the workload

**Status:** Accepted, effective 2026-10-17.
**Scope:** OH-Goon roadmap, admission/backends, Agent Skills, Linux/WSL qualification and release policy.
**Does not itself change:** signed contracts, source binaries, policy enforcement, or previously issued qualification evidence.

## Decision

OH-Goon is a general-purpose workflow and agentic execution/orchestration engine. It must make ordinary valid workloads easy to execute, cancel, inspect and recover on supported Linux/WSL hosts. Its default product roadmap is not a high-security host certification project.

Use one canonical lifecycle: Plan → Goons → Steps → original Enforcer → original LACE / chosen backend → original Step executor → original Game Tape. Workload and backend capabilities must be explicit, immutable, validated once, and consumed by the original owners. No parallel agentic or high-security execution engine is permitted.

**Standard profile:** Require correctness, explicit effects/consent, suitable selected backend capabilities, reliable process supervision, sensible isolation, cleanup and outcome reporting. Do not impose optional OS-exclusive writer custody, filesystem immutability ioctls, privileged unseal, special host trust-root provisioning or advanced security certification as prerequisites for standard execution.

**High-security profile:** Remains explicitly selectable, optional and separate in its capability/qualification requirements, not in its execution pipeline. Only selected guarantees apply. Inadequate backend/host support fails closed before work; never downgrade silently. Advanced work resumes only for an identified workload, threat model and measurable acceptance criteria.

**Historical implementation:** Preserve previous Enforcer/LACE code, tests, signers and security-related decisions unless a demonstrated defect warrants a focused fix. Existing stricter refusal paths must be deliberately scoped by the selected workload/backend rather than bypassed globally. No assertion that currently blocked Agent Steps are already runnable.

## Immediate engineering effects

Prioritize real Atomic/Composite/multi-Goon runs; real `.goonskills` execution and bounded agentic loops; ordinary Linux/WSL qualification; cancellation/cleanup; and replaceable backends. Standard sandbox support is P1. Defer exhaustive FD/mount/cgroup/namespace custody, privileged sealing/unsealing and high-security Gate A/B/C/certification without a specific requirement.

This decision describes product direction. Current implementation is established
by the checked-out source and its executable tests. Historical roadmaps and
checkpoint proofs are retained separately and do not set the requirements for
an ordinary development checkout.

## Acceptance criteria for this ADR

- The active TODO lists functional execution, Agent Skills/loops, standard Linux/WSL, cancellation and canonical backend contracts as P0.
- Advanced security work is explicitly deferred and no longer appears as the NEXT mandatory checkpoint or global ordinary release blocker.
- Any high-security unsupported guarantee still fails closed when *requested*; no silent fallback or safety downgrade.
- One original execution/authorization/observability lifecycle is preserved, with no new runner, signer, permission authority or diagnostic dependency.
- Ordinary `tests/`, `coverage`, Linux/WSL `qualification`, and opt-in high-security `certification` are distinguished.
