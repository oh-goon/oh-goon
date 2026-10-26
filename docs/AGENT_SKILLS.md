# Agent Skills

For configurable Ollama, LiteLLM, OpenRouter, OpenAI and custom model endpoints, see
[Agent providers](AGENT_PROVIDERS.md). The native review–repair template uses
these profiles through the existing supervised Agent Step; legacy local-model
harnesses retain their explicit Ollama contract.

OH-Goon runs Agent Steps through the existing Plan → Goon → Step lifecycle.
The Resolver captures the selected repository and Skill resources, Fleet owns
admission and sequencing, the existing Step supervisor owns native processes,
and Game Tape records their outcomes.

The workflow below uses built-in bounded model adapters. The
[runtime ownership inventory](development/AGENT_RUNTIME_OWNERS.md) maps their
current call paths and the native coding-agent integration. To run installed
OpenCode, Pi or Codex, use the [managed launcher](AGENT_PROVIDERS.md#run-a-native-coding-agent).
The shipped [native coding Plan](../templates/plans/agents/native-coding/README.md)
and [native review Plan](../templates/plans/agents/native-review/README.md) select
the implementation independently of their authored roles. Native configuration,
reasoning, tools and compaction remain owned by that implementation; Skills and
agent claims retain the same authority limits described here.

Reusable review and development Skills are maintained in
[agentsCookbook](https://github.com/Hugloss/agentsCookbook). OH-Goon accepts
caller-selected Skill resources through its existing source contract. A cookbook
checkout is optional: the engine builds, tests, and runs without one. Skill text
does not grant tool access, writes, model recipients, or verification authority.

The current local model interface is deliberately bounded. It can inspect
captured files, propose one file replacement, and interpret a failed test's
declared diagnostic. An operator can explicitly recapture repaired source for a
separately admitted attempt, or authorize an automatic repair/retest loop with
explicit attempt, overall-deadline and per-Step limits.

## Native Agentic Review–Repair Loop

Start with [agents/agentic-review-repair-loop](../templates/plans/agents/agentic-review-repair-loop/README.md)
and its [setup example](../examples/agentic-review-repair-loop/README.md).
The Plan imports the Composite Goon without repeating inputs. The Composite
Goon declares ordered imports with `phase: review`, `adjudicate`, `repair`,
`regression` and `verification`. A phase can contain Composite Goons; every
concrete Step belongs to exactly one phase. Missing, overlapping, interleaved
or partially classified phases fail before work. Ordinary unclassified Plans
retain their existing execution behavior.

For a single provider review, import `agents/review` without `phase:`, bind its
`skill` and use the normal `lock run.yml` / `run run.yml` commands with one matching config selection. Review-pass and
repair-write limits apply only to loops. Adjudication and proposal Agents consume
the preceding phases' admitted records and require the loop context.

Reviews run sequentially on the same frozen candidate and explicit complete
repository scope. Each selected Skill closure is captured with its resources.
The initial template selects four agentsCookbook reviews, but the controller
accepts any registered Skills and preserves user-declared review order.
The native provider adapter receives the full captured closure without silent
truncation. A request larger than its configured byte budget fails incomplete.

Review records bind the occurrence, candidate, scope and Skill identities.
Completed CLEAN and FINDING records account for every captured repository path.
On the bounded wire, an Agent can explicitly declare
`reviewed_paths: {"complete_scope": "<exact scope_identity>"}` instead of
repeating the full path list. This remains a claim that it reviewed the complete
captured scope, not permission to inspect only bounded evidence. Admission expands
that exact scope to the canonical path tuple; retained observations and public
records keep every path. Verified records use the same compact form in later
phase context. Partial reviews retain explicit path lists and cannot become
complete through compaction. Findings carry exact source hashes and line references. `LEAVE_ALONE` and
`INSUFFICIENT_EVIDENCE` remain distinct and cannot satisfy the strict all-CLEAN
goal. These are admitted Agent claims, not independent proof of semantic
correctness. Skill text and model output confer no effects or certification.

All safe reviews finish before a separate read-only Agent adjudicates the
findings. Every finding gets an explicit disposition. A coherent repair queue
partitions admitted findings without dropping secondary evidence; independent
defects remain accounted for. Each queue entry requires a behavioral regression
expectation and the proposal identifies changed regression sources. Native
regression checks and the next review establish the observable result; merely
calling a file a test does not prove that it detects a defect.

The proposal Agent has no write tools. Its bounded create/update/delete batch
is consumed by the ordinary native `oh-goon agent apply` Step. The existing
repair owner supplies explicit invocation write roots and candidate identity
before admission. Apply preflights the entire batch and every source preimage;
paths cannot escape those roots or traverse symlinks. Replaced workspace or
parent directories are rejected before publication and checked for convergence.
A failure during native
multi-file publication reports INCOMPLETE with affected paths and stops. It
does not qualify a partial repair or overwrite concurrent edits to roll back.
Batch replies are bounded to 65,536 UTF-8 bytes, 32 file operations and 32,768
bytes per replacement. Create targets use existing directories. Native command
argument limits also apply to the selected write roots. These limits are checked
before their responsible effect; an unavailable proof never becomes CLEAN.

Select `repair.review_passes`, `repair.write_roots`, `repair.timeout_seconds`
and `repair.step_timeout_seconds` in the same `run.yml`.
Five review passes permit at most four repairs, reserving fresh re-review.
The existing bounded Fleet repair driver admits a distinct linked Run for
each phase. It retains native outcomes, deadlines, cancellation, teardown,
source deliveries and Game Tape; it adds no scheduler, journal or issuer.
This current contract supersedes the retained Agent workload ADR's original
single-Run iteration proposal while preserving its execution ownership boundary.
Every repaired candidate starts again at reviewer one. Unchanged/repeated
candidates stop without consuming another repair. A stale Plan, Skill or
candidate, changed resolved command/provider configuration, failed phase,
incomplete evidence, exhausted limit, cancellation or
timeout stops the invocation. Rerun/readmission cannot inherit expired limits.

Completion requires fresh accepted CLEAN from every reviewer and successful
native final verification on the same candidate and scope. The returned
`converged` result is scoped workflow convergence, separate from APP
certification. A failed verification remains its original failed Run.

`oh-goon run run.yml --json` includes a structured `campaign` projection
alongside its invocation receipt: linked Runs, source-bound review findings and
adjudications, admitted candidates, repair output metadata, and selected/native
verification results. Findings retain their originating Run and Step, source
path, digest and line. A later CLEAN claim does not independently establish an
individual finding's resolution. Bounded native context diagnostics and original
execution facts explain preflight failures without including model/tool transcripts;
their existing claim and execution authority labels remain distinct.

Use `oh-goon plan campaign CAMPAIGN_OR_RUN_ID --json` with the campaign ID or any
linked Run to inspect the same retained evidence after interruption. The campaign
ID also works when initial admission was refused and no Run exists. Cancellation
before source/intent preparation reports that no retained campaign is available.
The authenticated `GET /api/fleet/runs/campaigns/{campaign_id}` API delegates to
the same read owner, including campaigns with zero admitted Runs.
The command reads one original journal cut;
it neither loads raw model transcripts nor resumes work. Missing Runs, absent
Step results and nonterminal or cancelled Runs remain incomplete. Committed
controller intent, exact phase linkage and decisions reopen from the canonical
Journal independently of invocation JSON. Successful child verification without
the subsequent committed controller decision reports
`controller_result.availability: unavailable` and `result: incomplete`; those
Runs alone cannot establish convergence. A fresh authorized invocation is
required for continuation; readback starts no work.

Regression and final verification consume the Step owner's successful settlement
and verified cleanup. An exit declared in `success_codes`, including a nonzero
exit, retains that accepted meaning in campaign progression and readback. An
undeclared nonzero exit remains failed; membership, candidate, supervision and
cleanup requirements still apply.

## Hashmarks dogfood acceptance

This is the shared acceptance contract for the native Codex campaign and the
[Hashmarks evidence handoff](https://github.com/Hugloss/Hashmarks/blob/main/docs/integration/OH_GOON_INTEGRATION.md#evidence-across-repair-candidates).
Use the [native campaign example](../examples/agentic-review-repair-loop/README.md#native-codex-service-campaign)
and existing owners; this contract adds no runtime admission rule or result store.

Installed deterministic qualification and the first live Hashmarks repair are
separate evidence. The existing
[installed campaign cases](../backend/tests/integration/services/test_native_codex_service_run.py)
run the selected real Codex executable, native tools, scoped apply, regression,
fresh capture and journal readback against a small synthetic repository. Their
local Responses fixture supplies deterministic model replies and synthetic native
state. Those cases qualify lifecycle behavior; they do not establish autonomous
reasoning quality, real-account readiness or a successful repair of Hashmarks.

| Scenario | Required observed behavior |
| --- | --- |
| CLEAN | Every selected review returns accepted CLEAN on the admitted candidate; native final verification passes; no repair is applied. |
| One known defect | A passing baseline and intentional mutation establish the defect first; source-bound findings lead to admitted proposal/apply, passing regression, fresh CLEAN review and passing verification. |
| Multiple independent defects | Distinct findings, their adjudication and repair queue membership remain accounted for; each repair has regression evidence and the final candidate receives fresh review and verification. |
| Interrupted repair | Cancellation stops further phase admission and unauthorized writes; native cleanup and retained Run evidence are inspected; incomplete work remains incomplete after reopening the original journal. |

The installed cases cover those four scenarios and an additional complete repair
with a configured MCP server whose tools remain disabled in every reasoning
phase. Native metadata discovery may initialize that registered server; no model
turn is submitted until workload exclusion is established. For the first live
acceptance, use a disposable Hashmarks candidate,
the four pinned cookbook reviews in declared order, and real native model
inference through a compatible explicitly selected `codex_service` profile.
Complete the known-defect path through every phase. A preflight rejection or a
successful single Agent task does not satisfy that live milestone. The
[Hashmarks workload recipe](https://github.com/Hugloss/Hashmarks/blob/main/docs/integration/OH_GOON_INTEGRATION.md#deterministic-narrow-dogfood-workload)
defines the narrow glob mutation; an intentional mutation is never reported as a
newly discovered upstream defect.

Keep preparation and execution evidence outside source and preserve these exact
facts in the existing export receipt, lock, invocation result and Run evidence:

- Original source commit, admitted repository/candidate identities, and the exact
  export exclusions. Exclude native project configuration by path before reading
  or copying members; preserve the original native files. An exported candidate
  has its own Git database and its own admitted review scope.
- Loaded Hashmarks producer implementation identity, selected through Oh-Goon's
  `backend/pyproject.toml` and `backend/uv.lock`. The target's own committed
  `uv.lock` owns its native baseline and verification environment. Establish the
  unmodified baseline and mutation failure there; an ambient environment is not
  a substitute.
- `goon.lock` execution digest, exact cookbook Skill pins and declared review
  order. Recapture may refresh candidate bytes while retaining resolved execution
  configuration and producer qualifications.
- Native executable digest and public runtime selection, service profile/model,
  repository and Skill roots, authorized write roots, review/timeout budgets, and
  exact regression/final-verification commands. Native credentials and private
  configuration contents are never evidence payloads. Keep the operator-owned
  known-defect verifier outside the repair write roots, and establish its passing
  baseline and seeded failure before the campaign.
- Linked Run IDs, source-bound findings and adjudications, candidate transitions,
  native Step exit/cleanup results, and selected, executed and unverified checks.
  Preserve exact verification membership when supplied; do not infer test success
  from a model claim or a command description.

Campaign policy decides workflow convergence and retains its decision in the
canonical Journal; original Runs and settled Step evidence decide execution
outcomes. Inspect `plan campaign CAMPAIGN_OR_RUN_ID --json` after reopening the runtime;
the verified Journal read cut retains campaign intent, exact linked phases and
committed controller decisions independently of invocation stdout. Successful
child verification without a committed controller decision remains incomplete,
with the missing decision explicit. Readback never resumes work or authorizes
publication. A fresh continuation needs fresh
caller authority, capture and limits. PR preparation may use these facts; pushing
or publishing a PR remains a separate explicitly authorized action.

## Prepare a development checkout

From the repository root on Linux or WSL:

```bash
make init
```

This installs the mandatory Hashmarks dependency through native uv using
[`backend/pyproject.toml`](../backend/pyproject.toml) and the committed
[`backend/uv.lock`](../backend/uv.lock). Hashmarks owns repository identity and
repository evidence; OH-Goon consumes it through its
[integration boundary](architecture/hashmarks-oh-goon-authority-boundary.md).
Native package-manager failures remain installation failures. An importable
host package or a test substitute does not replace the locked runtime.

Application startup verifies the loaded Hashmarks runtime before opening state.
Every model-backed Agent Step obtains Hashmarks task evidence from its frozen
repository before the first model request, including provider review,
adjudication and proposal. The producer's presentation and qualifications lead
the model context. The original packet is included in transient supervised Step
output; durable evidence retains hashes, byte counts and line counts for the
Step streams.
Typed review/adjudication observations persist separately. Historical Game Tape
does not retain or reconstruct the raw packet or model/tool transcript; see the
[current evidence limits](development/AGENT_RUNTIME_OWNERS.md#results-limits-and-observations).
Unavailable evidence stops the Step before model disclosure. There is no
evidence opt-out. Complete review and verification obligations still apply;
retrieval bounds cannot narrow the caller's admitted scope.

Local model workloads use Hashmarks for repository evidence, `rg`/`head` for exact captured resource delivery, and a running local Ollama
server, and an explicitly selected, already installed model. Source
initialization does not download Ollama or models. Model prompts are delivered
to the selected local recipient; a Skill file does not select another recipient
or grant shell, network, or repository-write capabilities.

## Current execution surface

| Capability | Current behavior | Boundary |
| --- | --- | --- |
| Skill source | `.goonskills.yml` maps local resources and groups remote directories by Git source/ref. Complete captured resources travel with the frozen handoff. | `goon.lock` records remote commits; a cache miss restores the same commit. |
| Local read-only tools | Selected harnesses can request bounded repository retrieval, list repository/Skill paths, and read admitted captured files. | Hashmarks observes and presents repository evidence with explicit bounds and source revisions. Exact resource reads use frozen bytes; the model cannot supply arbitrary shell commands or read arbitrary host paths. |
| Native reasoning/tool loop | Selected native Agents own conversation, tools, compaction and reasoning inside the admitted Step. | The former `local-model-skill-loop` mode is removed; re-author old inputs with native selection and obtain fresh admission. |
| File proposal | `local-model-proposal` can return a bounded replacement bound to one captured file's source digest. | A proposal is data and does not modify the repository. |
| Explicit apply and test | A later operator-authored command Step can invoke `oh-goon agent apply` for the explicitly selected path, followed by an authored test Step. | The apply owner checks the proposed source identity; the native test's real result remains authoritative. |
| Failed-test feedback | An explicitly selected earlier command output can feed a later `local-model-proposal` Step. | The recipient remains read-only, and an earlier failed test keeps the Run failed. |
| Automatic repair | A caller-bounded test → proposal → apply → retest sequence can repeat with fresh captures. | Each attempt is a separate original Fleet Run; native test results decide continuation. |

Ordinary local execution selects `oh-goon run run.yml` and uses the
existing supervised host backend. This profile does not claim kernel sandbox
isolation. Its supported workload, process cleanup, and output contracts are
separate from optional high-security qualification. The
[execution-first decision](architecture/ADR_EXECUTION_FIRST_PROPORTIONAL_SECURITY.md)
sets that product direction.

Use the source CLI to inspect the exact options:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon run --help
uv run --offline --frozen --no-sync --project backend oh-goon lock --help
```

## Select a Skill

Create `.goonskills.yml` under the explicit `agent.skill_root`. Selecting `.`
places it beside `run.yml`, including when the config selects a shipped Plan.
That same root owns local Skill resources; there is no catalog-directory fallback.
Declare each remote collection once, then list the Skill directories you want:

```yaml
sources:
  matt:
    source: https://github.com/mattpocock/skills.git
    ref: main
    skills:
      - skills/engineering/tdd
      - skills/engineering/codebase-design
local:
  team/review: skills/review
```

This exposes `matt/tdd`, `matt/codebase-design`, and `team/review` through the
existing `skill_reference` input. Remote paths are relative to the repository;
local paths are relative to the caller's Skill root. The namespace belongs to
OH-Goon and does not rewrite upstream `SKILL.md` content or names. Choose distinct
directory names within a namespace; conflicting aliases are rejected.

Native Git prepares each declared source once per capture. It accepts branch,
tag and full commit selectors. The first `oh-goon lock` records each remote
source/ref, selected directories and exact commit in `goon.lock`. Later locks
and runs keep those commits. Use the same lock command with `--update-skills`
to refresh them or accept changed source declarations. Cache misses fetch the
recorded commit; they never advance a branch implicitly. Commit the registry
and generated lock with the workflow. There is no INI registry or Gitlink mode.

Shared Agent inspection, preview, lock preview and execution consume remote Skill
pins from `goon.lock` beside `.goonskills.yml` in the explicitly selected
`agent.skill_root`. First lock that selected workflow through the CLI; then use
that same absolute Skill root for the Shared Plan. The Shared Plan receives its
own reviewed execution digest from the captured source. This consumes only the
existing Skill pins, without refreshing them or treating the other workflow's
Plan fingerprint as Shared Plan authority. A missing lock or changed registry
continues to refuse remote Skill capture; use the explicit CLI lock/update flow.

The managed Git object cache lives under
`${XDG_CACHE_HOME:-~/.cache}/oh-goon/skill-sources`. Native Git retains selected
objects, and temporary worktrees close after capture. Cache locations do not
participate in execution identity. Generic storage pruning does not delete
this cache; explicit removal while no captures are active is safe, and the
next capture restores the locked commits.

Before materialization, native Git evaluates indexed attributes and effective
filter configuration. Acquisition refuses selected configured smudge/process
filters and required filters, preserving the no-project-code boundary rather
than substituting unfiltered source. Unrelated native filters, credentials and
configuration remain available.

Selected directories retain their supporting files and relative layout. Git
metadata is excluded; repository cache-name filters do not discard Skill
resources, such as a committed `coverage/` example directory.
OH-Goon resolves and captures them once, then transports those exact bytes
through the existing Agent Step. Skill imports do not execute scripts, initialize
submodules, install packages, configure providers, or define Step order. The
Agent Step keeps its existing task input and one selected Skill. OpenCode/Pi
configuration remains owned by those native agents; importing their Skill
resources does not add an execution adapter for them.

The [cookbook example](../examples/agent-skill-cookbook/README.md) demonstrates
an exact upstream commit. The
[development-tool boundary](development/AGENTS_COOKBOOK_STRUCTURE_DOGFOOD.md)
links canonical review methodology for contributors working on OH-Goon itself.

The three local `SKILL.md` files shipped under `examples/` are small product
fixtures. They teach and test the source format; reusable review methodology
continues to be owned by agentsCookbook.

| Local example | Purpose |
| --- | --- |
| [Authoring](../examples/agent-skill-authoring/README.md) | Inspect named inputs and capture a local Skill; its `opencode` Goon is preview-only. |
| [Offline resources](../examples/agent-skill-offline/README.md) | Verify that a Skill, references, script resource, and asset survive source capture and relocation together. |
| [Local search](../examples/agent-skill-local-search/README.md) | Execute recipient-free frozen-source search through the original fixed-string Step and locked Fleet workflow. |

Agent authoring binds repository, Skill and acceptance inputs explicitly.
Select repository/Skill roots and acceptance in the `agent` group of `run.yml`.
Model-backed harnesses also require their native implementation/profile or managed
model profile; recipient-free read-only `local-search` does not select a model.
The config supplies no
native model metadata or private credentials. Normal `config inspect` remains
local and cannot fetch remote Skill sources; explicit locking uses the original
source capture owner. No stored selection grants fresh effect consent.

## Capture a repaired source for another attempt

The Python service interface supports an explicit capture boundary. After an
authorized apply, call `FleetPlanResolver.recapture_agent_step_handoff` with the
previous `FrozenAgentStepHandoff`, its expected handoff identity, and the same
Plan, inputs, authorized repository/Skill roots and acceptance selection. Only
the repository bytes may change in this repair request. A changed Plan, Goon,
source selector or Skill requires a new authorization. A cancellation callback
can interrupt capture, including file reads, without returning a next handoff.

For remote Skills, pass the original resolved Plan's `skill_sources` as
`locked_skills` during recapture and resolution of a supplied handoff. The
receiver verifies the captured delivery and supplied pins; it does not reopen
the registry or reacquire Skill bytes.

The previous handoff and its encoded packet remain unchanged. Resolve the new
handoff with `resolve_frozen_agent(..., handoff=new_handoff)`, review
and write its execution lock through the existing lock owner, and explicitly
submit `FleetPlanService.run(..., agent_handoff=new_handoff)` under a new Run ID.
Fleet verifies the lock and transports that selected packet; admission and Step
execution do not recapture live repository or Skill files. Omitting
`agent_handoff` preserves the ordinary capture-at-run-entry behavior.

Each Run retains its own source identity, native test result and Game Tape. A
passing retest does not rewrite the earlier failed test. Manual recapture does
not start another Run. The automatic mode below uses this same capture boundary;
the original Run lifecycle still owns execution and cancellation.

## Automatic bounded repair

The internal `FleetPlanService.repair` service can execute an explicitly
authorized four-Step workflow: native test, `local-model-proposal`, native
`oh-goon agent apply`, and native retest. The operator selects the repository,
Skill, model, tools and exact apply path in the existing Goon/Plan contract.
The model cannot change those selections or increase the caller's budget.

The test must use `on_failure: continue`, one native attempt, and a declared
`stream: stdout` or `stream: stderr` diagnostic bounded to at most 2048 bytes.
The Agent selects that exact output and a FAILED-only condition. Apply runs only
after a completed proposal; retest runs only after a completed apply. Retest
declares the same diagnostic output name and also permits a continued failure.
All four Steps use the selected repository workspace and one native attempt.
For example, an operator-owned `goon.yml` can contain:

```yaml
inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
  - id: check
    run: verify-project
    on_failure: continue
    outputs:
      diagnostic: {stream: stdout, max_bytes: 2048}
  - id: propose
    uses: agent
    harness: local-model-proposal
    task: Repair the failing check in src/module.py using the selected Skill
    feedback: "${{ steps.check.outputs.diagnostic }}"
    when: {kind: step_status, step_id: check, statuses: [FAILED]}
    requested_effects:
      recipients: [ollama_local]
      model: YOUR_INSTALLED_MODEL
  - id: apply
    run: "oh-goon agent apply --proposal ${{ steps.propose.outputs.proposal }} --expected-path src/module.py"
    when: {kind: step_status, step_id: propose, statuses: [COMPLETED]}
  - id: retest
    run: verify-project
    on_failure: continue
    when: {kind: step_status, step_id: apply, statuses: [COMPLETED]}
    outputs:
      diagnostic: {stream: stdout, max_bytes: 2048}
```

`verify-project` is your selected native test executable. It writes its bounded
diagnostic to the declared stream and returns its real exit code. For source
installations, the apply command may use
`uv run --project /absolute/path/to/oh-goon/backend --offline --frozen --no-sync oh-goon agent apply ...`.
The ordinary apply CLI owns path selection and source-digest validation.

The normal public setup uses the shipped bounded review–repair Plan and a
single `run.yml`; see its [example](../examples/agentic-review-repair-loop/README.md).
The direct four-Step repair service described here retains its original explicit
internal `AgentRepairBudget` contract. It is not a second public CLI configuration
route.

All three limits are required; there is no implicit repair budget. The attempt
ceiling is 1–32. The deadlines travel with each admitted target and bound native
processes through the existing Step supervisor. Cancellation stops the active
Run and prevents another capture or attempt; owned cleanup remains with that
supervisor. The Python interface takes an immutable `AgentRepairBudget` and can
also receive a cancellation callback.

Continuation requires a failed native retest with admissible current feedback,
unchanged Plan/Goon/Skill authority, and remaining budget. A passing initial test
starts no model or apply. A passing retest stops the loop. Exhausted attempts,
cancel, timeout, missing/malformed/oversized feedback, a failed/stale apply, a
changed source contract or unresolved execution all stop further repair work.
Native stream diagnostics come from the current attempt's capture, not an old
repository file. General single-Run file-based outputs remain supported.

The initial lock is never rewritten automatically. Subsequent Runs contain the
new frozen source identity, attempt ordinal, parent Run and original limits.
The result receipt lists the admitted Runs and a stop reason. `test_passed`
means the selected native verification passed; original failed Steps and Game
Tapes remain failed, including a failed check preceding a successful retest.
The committed decision retains `passed_test_step_id`, the policy-selected native
test occurrence in its final Run; publication verifies that exact accepted Step
and settled cleanup rather than promoting the Run's earlier failed checks.
Generic rerun/readmission cannot inherit a repair budget. A restart does not
automatically resume the loop; existing Fleet recovery governs any active Run.

The neutral Agent execution contract replaces older serialized Agent Steps.
Old configuration and locks require explicit re-authoring and re-locking; they
are not converted or interpreted through compatibility aliases. See the
[state cutover contract](development/AGENT_RUNTIME_OWNERS.md#execution-state-cutover-and-recovery).
This does not establish real Ollama model quality, native Linux/WSL qualification,
or release certification.

## Failed-test feedback

The producer must be an earlier command Step with `on_failure: continue`, a
declared bounded output file, and one permitted attempt. The receiving
`local-model-proposal` Agent selects one exact
`${{ steps.test.outputs.diagnostic }}` reference and a matching
`FAILED`-only `step_status` condition. The original sequencer binds the actual
published scalar; the model receives it as escaped **UNTRUSTED DATA**.

Feedback must be nonempty and at most 2048 UTF-8 bytes. Missing or oversized
diagnostics are refused before the model launches; a successful producer skips
the failure-only Agent. Feedback does not replace the operator task, captured
source, selected model, or admitted tool set. It does not cause a retest, create
a fresh source revision, or turn a failed native test into success.

The current behavioral examples are the
[explicit proposal/apply/test sequence](../backend/tests/integration/test_agent01b3j_authorized_proposal_apply.py),
and [failed-test model feedback](../backend/tests/integration/test_agent01b3l_original_fleet_model_feedback.py)
integration tests. Their deterministic model fixtures verify protocol and
lifecycle behavior; they are not evidence of real model quality.

## Run native local-model qualification

With Ollama running locally and a model already installed, pass its exact name:

```bash
OH_GOON_AGENT_QUALIFICATION_OUT="${XDG_STATE_HOME:-$HOME/.local/state}/oh-goon/native-agent" \
  bash scripts/qualify_agent_local_model.sh YOUR_INSTALLED_MODEL
```

The script checks the selected local model, materializes the committed backend
dependencies through native uv, and exercises real Hashmarks, real Ollama,
the original Fleet Step supervisor, frozen Skill resources, and reopened
SQLite/Game Tape. Output defaults to
`${XDG_STATE_HOME:-$HOME/.local/state}/oh-goon/qualification/agent-local-model`,
outside the source checkout. `OH_GOON_AGENT_QUALIFICATION_OUT` selects a different
output directory, as shown above. The selected `dev`, `quality`, and `build`
groups match `make init`, so qualification retains the prepared development tools.
Native uv reuses satisfied
packages and its download cache; Ollama retains the already installed model in
its own model store. The script does not install or pull a model. Each invocation
runs the native tests again and does not certify a complete repair loop or a release.

The script currently covers native read-only review and Skill-resource cycles.
Failed-test feedback and proposal/apply/test have separate deterministic
integration coverage. An unavailable model, dependency failure, skipped native
test, or rejected model response must not be reported as a native-model pass.
