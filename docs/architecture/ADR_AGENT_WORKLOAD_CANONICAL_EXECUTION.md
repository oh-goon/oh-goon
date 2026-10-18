# ADR — Agent Workloads Use the Canonical OH-Goon Execution Path

- **Status:** Accepted as a design decision (not yet merged into OH-Goon source)
- **Date:** 2026-10-13
- **Scope:** OH-Goon Plan/Goon/Step, Resolver, Enforcer, LACE, execution controller, sandbox backends, journal, Game Tape, agentic loops
- **Suggested repository destination:** `docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md`
- **Related:** X0 single-owner/single-execution-path invariant; B12E Enforcer/LACE physical-containment consolidation; AG-A–AG-F agentic-workload roadmap

## Decision

> **We will not build an agent runtime inside OH-Goon. We will extend the existing OH-Goon runtime to safely host agent workloads.**
>
> All executable workloads, including agents and repeated agent steps, MUST traverse the same canonical compilation, admission, sequencing, execution, physical-containment, recovery, evidence, and outcome authorities already used for ordinary Steps. A new workload kind MAY supply a narrow adapter, but MUST NOT introduce a parallel authority-bearing code path.

**One code path. Multiple workload kinds. No duplicated authority. No agent-specific security bypass.**

## Context

OH-Goon is extending its templates from ordinary command and typed Steps to reusable skill-driven agent workloads and bounded review–repair–review loops. Existing agent harnesses (e.g., Codex, Claude Code, OpenCode, Pi) already own model reasoning, their own agent loops and tool selection. Duplicating those harnesses or building an agent-specific executor would create inconsistent authorization, containment, teardown, restart and evidence semantics.

The primary security goal is to minimize unauthorized data exposure and effects using Enforcer authorization and LACE physical enforcement—not instructions asking agents to obey a policy. A compromised, confused or prompt-injected agent remains untrusted.

## Canonical ownership

| Concern | Exclusive semantic / authority owner | Allowed extension |
|---|---|---|
| Template and skill/repository resolution | Existing Resolver / canonical source graph | Add agent workload and skill-package inputs; freeze source closure once |
| Run/Step permissions, approved disclosure, budgets | Existing Enforcer | Extend the existing resource/capability contract for model/tools/repository/skill |
| Execution ordering and bounded iteration | Existing ordered Step sequencer / execution controller | Repeat compiled Step occurrences under the original Run; no parallel scheduler |
| Workload launch | Existing executor and selected execution backend | Narrow agent-harness adapter after admission, inside the same sandbox |
| Physical resources, mounts, process/network containment and observations | Existing LACE and selected sandbox mechanisms | Extend the original resource envelope and observation mechanisms, without a second fact publisher |
| Recovery, cancellation, generation/lease fencing | Existing Run/Step lifecycle and recovery owners | Include loop ordinal and step occurrence identity in the original lifecycle |
| Durable receipts and artifacts | Existing journal / content-store custody | Append immutable round evidence with original identities |
| Terminal classification | Existing Enforcer outcome owner | Derive loop completion from independently accepted evidence, not agent assertion |
| Presentation | Existing Game Tape | Read-only agent-round projections |
| Reasoning, prompting, context selection, coding strategy | External agent harness and selected skill | Backend adapter only; no OH-Goon reasoning framework |

## Mandatory invariants

1. **One authorized effect chain.** Every workload kind enters through the original Run/Step admission and follows the same selected executor/backend, LACE containment, teardown and outcome chain. There is no direct `agent` CLI bypass, alternate agent-launch daemon, independent Step scheduler, separate recovery service or second event writer.
2. **One semantic meaning.** Resolver freezes repository revision, full skill-package closure, tool schemas, runtime closure, model/provider policy, permitted disclosure destinations, loop body/limits and relevant inputs before admission. The agent must not re-resolve external inputs or expand authority at runtime.
3. **Workload-specific adapters have no authority.** An agent adapter only translates an admitted workload payload into the chosen harness invocation and observes results. It cannot grant filesystem/network/credential access, change backend, issue Run/Step identities or classify terminal outcomes.
4. **Physical boundary is authoritative.** Enforcer decides permissions. LACE and the selected backend enforce and observe physical effects. Prompt text, skill metadata, provider claims and remote tool results are untrusted and cannot grant access.
5. **Least-privilege agent-visible universe.** The sandbox contains only approved target-repository snapshot, selected frozen skill closure, audited minimal runtime, scoped tools and authorized artifacts. OH-Goon control-plane code, signing material, host credentials, other repositories and unrelated Run memory are not exposed. If the target is explicitly OH-Goon's source repository, the authorized source snapshot may be visible; live authority and secrets remain isolated.
6. **Remote channels are included.** Model inference, MCP, GitHub, LSP, artifact retrieval and other external transports must enforce the Enforcer-admitted repository/operation/data scope; LACE enforces available physical network boundaries. An endpoint unable to enforce required scope is not eligible. Explicit external inference means authorized disclosure, not zero egress.
7. **Monotonic loop authority.** Repeated Step occurrences remain children of one original Run and may not enlarge the admitted authority. Separate read-only review and staged-write repair Steps receive independently defined, pre-admitted scopes. No iteration grants itself new rights, changes limits or edits acceptance authority.
8. **No duplicate proof path.** LACE reports physical facts through the original fact owner and journal, and Enforcer classifies outcomes once. Development-only diagnostic paths and read-only Game Tape projections must never become alternate production enforcement paths.
9. **Fail before effects.** Unsupported agents, missing skill/runtime closure, ambiguous tool or network scope, unqualified backend, stale identities, unauthorized mounts or unprovable containment reject before work; post-start containment loss follows the existing fail-closed termination and recovery precedence.
10. **Verifiable completion.** Agent-generated `clean` is evidence to evaluate, not authority. Existing verification/result owners accept the declared scope and evidence before the existing Run outcome owner can report completion.

## Prohibited approaches

- A separate Ralph scheduler, agent loop engine or parallel Run lifecycle.
- `run: codex ...` / `sh -c` used as a substitute for the native typed Agent Step boundary.
- Agent-specific Enforcer, policy store, sandbox manager, journal, recovery owner, certification authority or terminal classifier.
- Direct agent/provider tool execution outside the admitted sandbox, including host-privileged MCP servers or unrestricted GitHub connectors.
- Passing control-plane source, host secrets, unrestricted memory, or unapproved model endpoints to an agent as “context.”
- Treating unit-test PASS, prompts, model assurances or a development physical diagnostic as production containment evidence.

## Implementation consequence

**Refactor existing owners before adding agent behavior.** Extend the canonical Step payload with a workload-kind-specific, validated payload and capability/resource needs. Ordinary commands and Agent Steps MUST converge on the same admission, allocation, launch ownership, cancellation, observation, teardown, recovery and reporting implementations. The only permitted divergence is a narrow post-admission workload adapter and backend-specific physical mechanism underneath the original executor/LACE contract.

The canonical sequencer may gain a bounded, serial loop construct within Composite Goons. Its iterations are uniquely identified Step occurrences within one admitted Run—not independently launched Runs. Each round seals receipts in original journal/content-store custody before the next round advances.

## Required acceptance evidence

- **Static owner census:** complete source-level inventory shows exactly one authority owner for resolution, admission, sequencing, sandbox allocation, LACE physical facts, recovery, terminal classification and journal publication. Architecture tests reject a second path.
- **Dynamic call-chain parity:** trace a Command Step and an Agent Step through the **same actual owners** with original signed Run/Step/iteration/generation identities. Backend-specific mechanisms and workload adapters may differ only where expressly permitted.
- **Containment adversarial suite:** planted canaries in OH-Goon control source, host credentials, other repositories and unrelated Run evidence; attempt local absolute/traversal/symlink/`/proc`/inherited-fd reads, remote MCP/GitHub reads, unauthorized model egress, runtime/package exposure and nested execution. No successful forbidden access or unauthorized data transmission in the qualified corpus; report scope and limits honestly.
- **Round isolation:** review Steps cannot mutate, repair Steps can modify only staged allowlisted paths, previous-round permissions cannot grow, unrelated memory remains inaccessible and source/skill identities cannot change silently.
- **Recovery/termination:** crash, timeout, cancellation, revoked epoch, backend failure and restart preserve one effect chain and existing terminal precedence; incomplete teardown proof does not become success.
- **Backend qualification:** Bubblewrap and any enabled remote backend satisfy the same semantic contract with independent native physical evidence; no silent fallback or inherited certification.
- **Regression:** existing command execution semantics and Plan/Goon catalog remain unchanged; every reproducible defect gets a focused regression.

## Gate and status

**Decision is an architectural constraint, not a claim of implementation.** The Agent Step and loop capabilities remain proposed. Native WSL and Enforcer/LACE original-issuer/physical-closure certification, including B12E-C2..C6, are independent prerequisites and may not be inferred from hosted development tests or template UX results.

**Definition of done:** An admitted Agent Step can run an existing skill on an explicitly targeted repository through the current OH-Goon execution chain; tests demonstrate that both local and remote tools cannot expose non-admitted information, all rounds are recoverable and auditable, and no second authority-bearing path has been introduced.

## Alternatives rejected

1. **Separate agent runtime/orchestrator:** duplicates policy, sequencing, lifecycle and evidence owners; increases attack surface and drift risk.
2. **Agent sandbox governed by prompt instructions:** cannot enforce filesystem, tool, network or credential boundaries.
3. **Shell wrapper around an agent harness:** may bypass native Agent Step capability and containment contracts.
4. **Duplicated agent-specific LACE policy/fact path:** contradicts X0 and risks divergent physical and terminal outcomes.

## Consequences

**Benefits:** consistent security across command and agent workloads; improvements to containment benefit all Steps; simpler architecture ownership; testable data-leak boundaries; reusable existing harnesses and skills.

**Costs:** extending the existing Step/executor/resource interfaces requires careful backward compatibility; provider tools and model egress need strict transport integration; real native/back-end physical qualification is non-optional; complex agent capabilities may stay blocked until the canonical path can enforce them.
