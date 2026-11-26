# Parallel agent work requirements

## Outcome and scope

Implementation is tracked as the next priority under the agent-loop program.
[HUG-225](https://linear.app/hugloss/issue/HUG-225) · [HUG-217](https://linear.app/hugloss/issue/HUG-217)

An operator can start two independent coding agents on one task and one registered
Git repository. Each agent receives the same captured base and acceptance criteria,
its own brief, and an exclusive writable worktree. While both Runs are active, the
agents can publish and read one another's ideas, questions, and dead ends. The
operator can see what each produced, which ideas crossed between Runs, and which
exact candidate passed independent verification. The operator chooses the change
to take forward. The developer checkout and its branch stay untouched.

The first delivery is opt-in on Linux using two fresh headless Pi candidates under
the existing Bubblewrap and native resource-scope contract, with managed inference,
one runtime and one database. Use two agents exploring alternative approaches to
one task. This bounds the first qualification without making the number of agents
or the chosen harness part of the product's permanent identity. Additional slots,
other harnesses, named branch workflows, durable native session reopen, combined
candidate promotion and organization-wide memory require separate scope and proof.
Ordinary serial execution keeps its existing default and configuration.

## Baseline and design rationale

HUG-221 provides owned detached worktrees, candidate checkpoints and explicit
retirement. HUG-223 qualifies two opt-in native Runs over distinct registered
subjects. Current admission refuses same-subject overlap even for distinct
worktrees because uncertain effects are quarantined by repository. HUG-220
provides fresh continuation from recorded context. Live peer notes and exact
candidate export are new behaviors in this proposal. [HUG-221](https://linear.app/hugloss/issue/HUG-221) · [HUG-223](https://linear.app/hugloss/issue/HUG-223) · [HUG-220](https://linear.app/hugloss/issue/HUG-220) · [Reviewed concurrency contract](https://github.com/oh-goon/oh-goon/blob/e064bc080a569d1c0b636e9049895dfd4d1e795d/docs/development/ARCHITECTURE_DECISIONS.md#explicit-capacity-for-two-independent-native-runs)

Spotify Xirp uses separate Git worktrees for coding sessions. Its Portal connection
lets active agents retrieve shared project knowledge and propose wiki updates.
Shirobokov et al. let independent teams exchange ideas and dead ends, while an
external verifier checked results against the same frozen specification. These
patterns motivate isolated editing, deliberate exchange and a verifier outside
the agent's control. [Xirp Projects](https://backstage.spotify.com/docs/xirp/projects) · [Portal wiki](https://backstage.spotify.com/docs/xirp/workspaces/wiki-pages) · [Quantum-program study, §§1.2 and 2.6 and Appendix C](https://arxiv.org/html/2611.09822v3)

## Required operator journey

1. Submit one explicit request with a repository, task, two briefs, permitted
   native selections, resource and time limits, a shared-notes selection, and the
   verification to run. Oh-Goon resolves the exact base and returns the durable
   request reference plus the planned child Run IDs. An operator can inspect the
   same reference after connection loss.
2. Start both as original Fleet Runs. Each sees its own code and harness state.
   Agents choose what to share. A peer can read and reply before either Agent
   Step ends.
3. Inspect both candidates, their exact verification outcomes and the note
   exchange. Stop one Run without stopping its peer, or explicitly cancel the
   whole request. Successful, failed, cancelled and unresolved children remain
   visible individually.
4. Select a verified candidate for export and confirm its exact base and change
   identity. Export gives the operator a reviewable artifact. Applying, committing,
   pushing or merging uses a separately authorized native Git workflow.

## Admission, isolation and recovery

**AW-01. Resolve and record launch intent.** Bind one registered Git subject,
expected clean base commit and captured source identity, canonical task and
verification selection, two distinct child IDs and briefs, native selections,
note access, and admitted budgets. Bind each child capture to the same base
identity while preserving that child's own source and workspace custody. Reject a
dirty, changed or unsupported source, invalid request, missing native capability,
or known capacity conflict at its responsible admission owner. The group is an
association between original Runs. Each child follows the existing Fleet
admission, claimed worker and Step path.

**AW-02. Order launch, replay and cancellation.** Persist the launch intent and
planned membership before dispatch. Use the existing admission transaction to
bind each admitted child to that intent. For replay, validate current read access
and compare the original authored request identity and payload before consulting
current HEAD, profiles or capacity. An exact replay returns the original resolved
intent, membership and current recorded states. A changed payload under that
identity conflicts. If one child cannot start after its peer
has started, retain the peer and show partial launch with the exact refusal,
failure or unresolved cause. A definitive refusal of one child leaves any
already selected sibling eligible for its own admission if its authority is
current. Group cancellation records a stop boundary, fences admissions ordered
after it and sends cancellation through each already admitted Run's owner.
After interruption, the existing Run owner reconciles membership against the
admission Journal and closes dispatch when it can prove the original controller
can no longer issue admissions for the request. A child whose admission cannot
be confirmed stays unresolved. A planned child with proven absence of admission
after dispatch closes is shown as never started. A read or request
replay observes that state. A fresh execution requires explicit admission.

**AW-03. Bind a distinct writable candidate to each child.** The workspace owner
allocates and resolves the candidate and source snapshot, binds the write lease
and validates the physical root. The existing worker and isolation owners bind
the process scope, private mounts, home, scratch and native harness state. Native
Git administers worktree registration and removal. Each agent can edit only its
own candidate. Peer worktrees, developer files, and shared Git administration
stay outside its writable and readable mounts. The current shared capacity and
per-worker ceilings apply. A third request at full capacity reports busy.

**AW-04. Bound uncertainty and retire through the original owner.** Permit
same-subject overlap only after proving candidate-local authority and disjoint
writers. Failure or uncertainty proven confined to one candidate fences that
candidate and its resources. Shared administration or effects whose extent is
unknown retain the repository-wide fence. Settlement and cancellation order
come from the original Run/Step/lease owners. Hold uncertain resources until
their original owner proves quiescence. Retain dirty or unpromoted work under the
existing workspace policy. A cleanup failure is visible alongside the original
Run outcome. The owner's retirement and restart reconciliation handle partial
Git effects and refuse ambiguous or foreign paths.

## Shared notes

**AW-05. Offer task-scoped live publication and reading.** Give each active,
admitted Agent Step a narrow, explicitly selected note capability in the
qualified harness mode. Bind membership and authorship to the admitted request,
Run, Step/Attempt, captured source and candidate. Agents post a short idea,
observation, dead end or question and can reply to a note. References identify
retained evidence and resolve through existing read authority. Validate the
caller on each operation. An unrelated Run, stale attempt, or cancelled writer
has no access through its former agent capability. An authorized operator can
still review retained notes.

**AW-06. Bound storage and make retries safe.** Persist notes as task coordination
data in the existing runtime state. Append and allocate IDs in commit order.
Paginate committed records by cursor, with explicit expiry and continuation
indicators. An exact retry of a committed append under the same author-bound
request identity returns the original receipt to the still-authorized writer
while it remains retained. Reuse with changed content conflicts. An authorized
operator can resolve a lost acknowledgement through read-only receipt lookup
after the writer loses its capability. Fence cancellation and append at the same
authorization boundary. A committed note survives cancellation and restart.
A refused or unavailable write reports its actual outcome, and a lost
acknowledgement is resolved through the original request identity. The sibling
keeps its own access while eligible.

Use versioned initial limits of 4 KiB UTF-8 per note, 100 notes or 256 KiB of
note bodies per task, and at most 20 notes or 16 KiB of bodies per read. Report a
limit refusal or continuation explicitly. Retain note bodies for up to 30 days
after dispatch closes and all children have known terminal or never-started
outcomes with settled custody. The existing retention owner protects quota-bounded
active or unresolved groups and in-progress reads. An expired cursor or append
identity returns an explicit expired result while its group is retained. Retain only bounded metadata
under existing Run evidence policy. A storage failure follows the existing
failure path and preserves already committed observations.

**AW-07. Deliver bounded, attributed context.** Prove an actual tool or turn
path in the selected harness that makes notes available while both Steps remain
active. Qualify this early in the exact installed Pi candidate profile, through
a narrowly admitted adapter path that preserves the current native configuration
boundary. The receiving Run explicitly reads notes through its scoped capability.
Apply its existing disclosure and model-recipient policy to delivery. Treat all
note bodies as untrusted data and label the author and observed source. A claim
from a different candidate, or an earlier source after content changes, stays
scoped to that origin. A workspace ID can identify where the note was written.
Treat content at the time of the observation as unbound unless retained evidence
identifies those exact bytes. Only admitted tools confer effects. Hashmarks still
supplies repository evidence against the receiving Run's exact capture, and the
native verifier still supplies verification results. Keep raw transcripts,
broad local state credentials, arbitrary paths and unrestricted file forwarding
outside the note interface.

**AW-08. Make exchange inspectable.** Existing Run readback and Game Tape show
the request, child states, note IDs, authors, source bindings, replies and
publication or delivery observations. Bind delivery to the receiving Run,
Step/Attempt, harness and admitted model recipient, immutable note IDs and
delivery outcome. Distinguish a completed tool transfer from a failed or
unobserved transfer. A claim of use requires separate evidence such as a reply
referencing the note. Apply current access and redaction rules. Evidence references
and expiry remain legible after a fresh-process read, with sensitive bodies excluded
from general receipts and diagnostic readback.

## Verification and candidate selection

**AW-09. Verify the exact candidate against frozen intent.** Freeze the task
acceptance criteria, adjudicating test inputs and expected behavior, and verifier
executable/configuration authority before agents edit. Use the existing
verification and native tool owners, with these adjudicating checks controlled
outside the agent-writable candidate. Keep required Hashmarks selection and
provenance intact where applicable. Bind the verdict to the original Run,
base/source identity, verifier identity and exact candidate content observed
by the verifier and at settlement. A failed check retains its result. A missing
or unsettled check, stale candidate or
unproven content binding remains incomplete. Agent notes, logs and exit status
alone cannot supply a verifier result. If verification changes the candidate,
verify its final bytes under fresh authority or report the mismatch.

**AW-10. Select and export exact verified changes.** Let the operator compare
the two candidate outcomes and explicitly select one. Recheck the content and
its admitted base against the settled checkpoint and verification receipt. Reuse
native Git and the existing artifact owner to export a bounded change for the
exact verified candidate. Report the export digest and identity. The export must
account for all selected changed, new and deleted files. Refuse out-of-scope,
unsupported or unaccounted changes explicitly. A changed candidate cannot inherit
its earlier verdict. A later combined candidate, if selected in a future
delivery, requires its own exact-source verification before it can be presented
as verified.

## Implementation sequence and ownership

| Order | Delivery | Start with |
| --- | --- | --- |
| 1 | Same-subject candidate admission and containment, with healthy overlap, cancellation and restart proof. Land this safety boundary before enabling shared notes. | Fleet plan and candidate ingress, Journal admission, the existing execution lease and quarantine owners, and `RepositoryWorkspaceAuthority`. |
| 2 | One explicit launch and readback path, stable child association, and live bounded notes for the first qualified harness. | The Fleet service, Journal/runtime state, selected native harness adapter, CLI, and existing Run/Game Tape readback. |
| 3 | Candidate comparison, exact verification binding and selection/export. Complete the operator journey and measure the value of note exchange. | Existing verification Step/result owners, candidate checkpoint and artifact owners, and Game Tape. |

The coding agent owns reasoning, edits and note content. Hashmarks owns repository
evidence and flat verification nominations. Oh-Goon owns Run admission, worktree
custody, capacity, coordination metadata, effects and verification-result
authority. Native Git retains its own semantics, and the native harness owns its
session and model interaction. Add operator-facing API fields through the existing
backend contract and generation path if the implementation exposes them. Use the
existing frontend readback for display. [Canonical execution lifecycle](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md) · [Hashmarks authority](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md) · [Agent runtime owners](AGENT_RUNTIME_OWNERS.md) · [Harness compatibility](AGENT_HARNESS_COMPATIBILITY.md)

## Acceptance evidence

| Proof | Pass condition | Requirements |
| --- | --- | --- |
| Same-repository overlap | Two actual claimed workers overlap and independently edit the same relative filename to different values. Both resolve the same base and have distinct candidates, scopes, writer leases, homes and scratch. The developer checkout and refs are unchanged. A peer file and shared Git metadata are inaccessible, and full capacity rejects a third Run. | AW-01, AW-03 |
| Invalid and interrupted launch | Dirty or stale base, unsupported mode and duplicate candidate allocation refuse at the responsible boundary. Exact request replay after a lost response and changed HEAD identifies the original children. Changed request content conflicts. Kill the controller between child admissions. Fresh readback distinguishes admitted, never started and unresolved children without replay. | AW-01, AW-02 |
| Cancellation and custody | Cancel and crash one worker during overlap in separate cases while the sibling verifies. Cancel the task between admissions and prove the second child cannot launch. A fenced writer cannot publish late effects. Unknown or shared effects retain the appropriate fence. Recovery preserves original outcomes and retirement depends on exact quiescence and workspace evidence. | AW-02, AW-04 |
| Live exchange | Agent A posts an idea, B reads it and posts a reply, and A reads the reply before either original Step ends. Show that peer-candidate or old-source notes keep their origin alongside fresh Hashmarks evidence. Fresh readback proves publication and delivery. Scope, expired access, wrong model recipient, size/quota overflow and arbitrary file-reference probes refuse through the relevant owner. | AW-05, AW-07, AW-08 |
| Concurrent notes and failure | Simultaneous appends appear once each, in commit order across cursor pages. A live retry returns the original receipt after acknowledgement loss and an authorized operator locates it after restart. Changed data conflicts. Order cancellation against append. Committed notes survive, stale agent capabilities fail, and an unavailable store cannot report a saved note. An expired cursor reports expiry. | AW-05, AW-06 |
| Independent result and export | One candidate passes the frozen checks and exports the exact verified bytes, with developer source unchanged. A failing candidate, stale checkpoint, peer receipt, modified acceptance checks and an agent note claiming a pass cannot produce verified export. Exercise new and deleted files and refusal for content the export cannot fully represent. | AW-07, AW-09, AW-10 |

Start with focused admission, persistence, policy and projection tests, then one
bounded genuine Pi/Bubblewrap journey for overlap and notes, using the existing
native fixture and selected host proof conventions. Use owning static checks
and the relevant candidate-bound merge proof when each delivery is coherent.
Public API or browser changes add the applicable generated freshness and
consumer proof. Missing environment-specific evidence remains unverified.

For product evaluation, use the same frozen tasks and acceptance checks across
one agent, two independent agents, and two agents with notes. Compare at
comparable total budgets and record verified completions, time to a verified
candidate, observed model usage where available, and attributable idea reuse.
Keep tooling smoke evidence, real task quality and cost evidence distinct.
Parallel editing can pass each agent's own checks and still fail at integration.
[ParallACT coding-agent study](https://arxiv.org/abs/2611.00314)

## Expansion after the first delivery

Use the first delivery to evaluate two workflows. Competing approaches can
start independently and exchange discoveries at a chosen point. Complementary
assignments can share interface decisions early and land through a separately
verified integration candidate. Increase the admitted agent count only after
the same workspace custody and access rules are demonstrated at the selected
capacity and the evaluation shows where extra agents improve verified outcomes.
Qualify additional harness modes against the same requirements.

Recurring collaboration uses fresh, individually bounded Runs. A later round
selects reviewed branch content and any exported note document explicitly.
Use native Git to apply an exact verified export onto an operator-selected
branch, then rerun acceptance on the receiving source. Live note permissions
belong to the admitted task and Step. A reviewed note document can travel as
captured context while writer capabilities, native sessions, and verification
authority remain scoped to their original owner.

Recurring time triggers belong to the existing Schedule owner. A selected
single-repository read-only review can recur on its exact captured source and
delivery through the [public local Schedule flow](../AUTOMATION_MODEL.md#recurring-native-reviews).
Pausing prevents new admissions. Existing Runs settle through their original
owners. The operator selects the branch, commit and reviewed Skill for a fresh
writable candidate through the [named-branch handoff](../AGENT_PROVIDERS.md#share-reviewed-context-and-continue-on-a-named-branch).

The [local writable round](../AGENT_PROVIDERS.md#advance-a-local-agent-branch-and-start-a-writable-round)
binds the selected predecessor, published branch commit and receiving source to
the original Fleet admission. The dedicated bare repository holds the branch and
its native publication witness. The separate registered checkout supplies exact
detached source. Replaying the same request reopens its original receipt or Run.
The successor runs the frozen baseline and outgoing verifiers in its own Step
lifecycle. A verified result may authorize another advance on the same lineage.

The [writable Schedule](../AUTOMATION_MODEL.md#scheduled-writable-rounds)
selects the next entry from one immutable, operator-approved task list. The
original Schedule occurrence records selection before publication or admission.
The original Fleet admission commits the actual successor target and its
Schedule binding together. Restart reopens the same selection, branch receipt
and Run. A failed predecessor or an exhausted task list stops further selection.

The [offline host handoff](../AUTOMATION_MODEL.md#move-scheduled-writable-work-to-another-host)
transfers the selected Git ref and reviewed task documents. The receiving host
starts its own qualifying Run from the transferred commit and records its own
verified branch seed. Restart evidence belongs to the receiving Schedule and
its local Journal.

Advance unattended collaboration in this order and verify each boundary on the
selected environment.

| Boundary | Required behavior and proof |
| --- | --- |
| A second environment | Give each runtime its own registration, state, tool configuration and model authorization. Transfer explicit Git source and selected task documents. Demonstrate fresh repository evidence and native verification there, with controlled restart or loss of either environment. Remote publication and model egress follow their independently authorized owner. |
| Cross-environment live notes | Admit task membership and note recipients on each worker, then prove simultaneous attributed delivery, cancellation fencing and retention on the selected transport. An exported document remains a practical handoff when no cross-host delivery is qualified. |

## Requirements review method

Use the project's [agentsCookbook pin](agents-cookbook-source.json) for lifecycle
closure, evidence binding, validation design and the implementation handoff.
These skills guide requirements review. The repository owners above supply the
product and execution contracts.
[Lifecycle closure](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/requirements-lifecycle-closure-review/SKILL.md) · [Evidence binding](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/evidence-provenance-binding-review/SKILL.md) · [Validation gap finder](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/validation-gap-finder/SKILL.md) · [Implementation dry run](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/implementation-dry-run/SKILL.md) · [Plan contract guard](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/plan-contract-guard/SKILL.md)
