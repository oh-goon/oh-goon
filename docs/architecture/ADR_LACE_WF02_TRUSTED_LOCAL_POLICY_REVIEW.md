# ADR LACE-WF-02 — Trusted-local execution policy review (approval pending)

**Status: REVIEWED PROPOSAL, NOT APPROVED, NOT ENABLED.**
**Scope: LACE-WF-02 human policy decision only; this ADR does not change backend behavior.**

## Problem, definitions and original architecture

WF-01 defines workload- and policy-specific required guarantees. It deliberately
marks `trusted_local_execution=not_approved` for every Plan execution purpose.
The existing `CicdExecutionPolicy.trust_repository_code=True` sets the
`TRUSTED_REPOSITORY` *threat model* and the **minimum** kernel boundary of
`SHARED_HOST_KERNEL`. It is **not** permission for arbitrary host execution.
Even trusted CI/CD still demands a private filesystem, private process tree,
enforced network policy, content-identified runtime, exact argv, disposable
attempt and immutable execution authority. Explicit resource caps add physical
CPU/memory/process enforcement demand. Treating `trust_repository_code=True`
as a trusted-local approval would be an unacceptable silent privilege change.

'Local' refers to the operator's own host/WSL machine, not to a weaker Plan
syntax or a replacement executor. A trusted-local *policy* may eventually
allow a specifically reviewed local execution profile; it does not assert
that this machine can physically enforce all effects. LACE must not invent
cgroup delegation, foreign-writer exclusion, mount custody or teardown proof.

## Proposed, explicitly optional trusted-local policy: TL-1

**TL-1 is a review proposal, not an enabled feature.** Human approval is
required for its complete scope before WF-03 may implement it.

| Dimension | Proposed TL-1 decision | Fail-closed boundary |
|---|---|---|
| Eligible workload | Human-controlled, operator-owned **CI/CD trusted repository** for a single local developer run | Untrusted repository, hostile generated code, arbitrary agent-written code, migrations, maintenance and other unreviewed purposes excluded |
| Caller | Interactive operator with verified local authority over selected repository and executor | No automatic agent, daemon, scheduled, headless, shared-fleet or remote fallback |
| Consent | Explicit, per-run, visible human acknowledgement of repository identity, exact resolved Plan, backend, filesystem/network reach and risks | Absence, stale revision, or changed Plan cancels request before effects; no inherited consent |
| Execution | Only the original Plan → Enforcer → Fleet → original selected LACE backend chain | No direct host subprocess, special shortcut, alternate queue or bypass of lock/source identity |
| Workspace | Exact scoped repository plus declared ephemeral outputs; refuse privileged files, host secrets and undeclared write paths | No implicit `$HOME`, SSH agent, Docker socket, host credentials, root, or ambient mounts |
| Network | Default deny except explicitly reviewed Plan egress through original backend | No unrestricted networking or bypass of the egress owner |
| Kernel resources | Actual backend operations must satisfy their own OS-proven native custody and teardown obligations | Never infer exclusive cgroup/mount/namespace custody from operator consent |
| Limits | If Plan explicitly requires CPU/memory/PID limits, backend must prove them; omission is not proof of unlimited safe capacity | No ignored caps, invented limits, or disabled enforcement for convenience |
| Observability | Original Run ID, Plan/source/lock identity, policy, consent scope, backend and original Game Tape outcomes | No parallel journal or fake `APP PASS`; preserved failure vs refusal semantics |
| Refusal | Stop before effects when trust, consent, capability, source, egress, custody or teardown requirements are unresolved | No downgrade to a local shell or another backend upon rejection |
| Lifecycle | Original cancellation, teardown, revocation and recovery semantics remain mandatory | No 'best effort' cleanup interpreted as containment proof |

The user may instead select **TL-0: trusted-local execution prohibited**,
which needs no new runtime feature, or **DEFER: keep status unchanged**.
If TL-1 is approved later, the *implementation* must use the existing
canonical policy, Enforcer admission and selected original LACE backend.
It should assess actual effects, not demand a universal fully mutable host
for authoring-only or no-effect actions. An inherently impossible backend
proof remains refusal, not a globally blocked product.

## Requested approval questions

Approval must unambiguously choose TL-0 / TL-1 / DEFER and, for TL-1, affirm
all of these dimensions: trust & authorship, per-run user consent, local-only
scope, source/lock binding, write/network visibility, effect-specific custody,
no default fallback, original evidence, and cancellation/revocation behavior.
Any proposed exception must be reviewed as a separate explicit policy change;
there is no blanket 'enable local' switch. This artifact is not an approval
record and its presence in a source archive confers **zero runtime authority**.

## Exact code owner and next steps

- `fleet_plan_execution_policy.py`: canonical Plan purposes, trusted flag and
  WF-01 non-authorizing `LaceRequiredGuarantees` projection.
- `fleet_plan_isolation.py`: original isolation demand and backend comparator.
- `_executor_placement.py`: original admitted placement/capability checks.
- `fleet_plan_process_isolation.py`: binds exact previously admitted backend.
- Bubblewrap and the existing LACE cgroup/network/mount/teardown modules:
  effect-specific custody and real observed capabilities; no new backend.
- Enforcer and Game Tape remain admission/proof and durable projection owners.

WF-03 must not be marked complete or authorize TL-1 until the human decision
is recorded and the relevant real backend semantics are qualified. WF-03 can
still improve strict-policy checks that are **independent** of TL-1 consent.
RUN-8 through RUN-11 are real execution milestones and require their own
native evidence. This policy review certifies none of them.

## Rejected shortcuts

- Treating `trust_repository_code` or an enum name as operator consent.
- Auto-detecting WSL and silently falling back to bare host subprocesses.
- `--unsafe` as a general downgrade, or signing an operator acknowledgement
  and calling it independent native OS kernel custody.
- Disabling all LACE checks because only a benign Iris workload is requested.
- Reimplementing the resolver, Fleet runner, verifier, local backend or Game Tape.

## Verification and status

The WF-02 regression ring establishes **the present non-approval contract**:
all current policy kinds remain `not_approved`; an attempted approved Pydantic
projection is refused; bare-host capabilities still fail trusted CI/CD demand;
and non-CI purposes remain explicitly undefined. This is *not* runtime TL-1
validation and does not assert any physical kernel guarantees.
