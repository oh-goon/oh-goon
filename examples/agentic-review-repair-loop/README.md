# Agentic Review–Repair Loop

Classification: runnable-with-prerequisites

Run four ordered pinned agentsCookbook reviews, adjudication, proposal, scoped native apply, regressions and final verification. Repository evidence is disclosed to the configured model; admitted repair can write only the explicitly selected roots.

**Prerequisites:** an initialized Oh-Goon installation, registered workload repository, explicit approved repository root, configured provider/model profile and captured Skills. The project must supply executable regression and verification scripts. Keep the workflow outside that workload source. A native campaign additionally needs an installed supported agent and qualified compatible profile. External/native availability is not established by this example.

Initialize a new workflow using the shipped Plan:

```sh
oh-goon init --template agents/agentic-review-repair-loop --kind plan --output ./workflow --draft \
  --repository /absolute/path/to/registered-repository
```

Run initialization from the intended workflow parent. The initializer includes the existing `.goonskills.yml` and local phase Skills, and records the target path and repository reference/root without registering it. A draft has no lock and does not run a model. Edit `workflow/run.yml` using [the complete provider selection](run.yml): choose the configured `agent.model_profile`, executable check paths and bounded `repair` group. Do not copy provider endpoints, keys or native settings into this file. The [registry](.goonskills.yml) declares the cookbook source once; `goon.lock` owns the captured commit. The local adjudication/repair Skills are product fixtures, not a review catalog.

| Caller selection | Required choice and path base |
| --- | --- |
| `target.repository` | The registered workload directory. Relative paths resolve from `run.yml`'s parent; prefer an explicit absolute path for this separate workflow. |
| `inputs.repo` + `agent.repository_root` | `local:my-project` means `my-project` beneath the absolute approved repository root; it must select the same directory as `target.repository`. It is a source path, not an internal repository ID. |
| `inputs.regression_command`, `inputs.verification_command` | Required executable script paths relative to the workload repository, not the workflow. The existing check Goons invoke each script directly, without appending arguments. |
| `agent.model_profile` | The model-backed campaign's configured model selection. The distinct native variant below uses `agent.implementation` + `agent.profile` instead. |
| `agent.acceptance`, `agent.skill_root` | Required acceptance and workflow-contained Skill root. Keep acceptance out of `inputs`; `skill_root: .` keeps the registry and local phase Skills together. |
| `repair` | Required explicit review-pass, write-root and timeout choices for this invocation. Write roots are relative to the workload repository. The values in the example are selections, not defaults imposed on other projects. |

The Plan/Goon declares input types and defaults. Inspection reports the default
designation and declaring owners for optional `goal`, `adjudication_skill` and
`repair_skill` inputs; override them through
`inputs` only when needed. Keep ordinary omitted defaults in their declaring
Goon rather than copying another schema into `run.yml`.

```sh
oh-goon workflow inspect ./workflow/run.yml --json
oh-goon workflow prepare ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon run ./workflow/run.yml --authorize-repair
```

Preparation explicitly acquires the selected remote Skills and writes the lock.
On a fresh cache, do that before validation. Inspection and validation are
read-only; neither executes the regression scripts or contacts a model.

The [shipped Composite](../../templates/goons/agents/agentic-review-repair-loop/goon.yml) owns phase order. Every review pass runs all four reviews over one captured candidate. Incomplete execution/evidence stops the phase. Findings lead to adjudication, proposal, native scoped apply and regressions; the next pass restarts at the first reviewer. Five passes allow at most four repairs. Completion requires fresh accepted CLEAN records from all reviewers with exact scope accounting and successful final verification on that same candidate.

The `--authorize-repair` action grants fresh consent for this invocation; it is required on each effectful run and is never stored in `run.yml`. A checked-in selection grants no fresh write consent. Interrupted/expired runs do not silently resume writes. Lock/source/provider/Skill changes need explicit refreshed authority. Native checks own their result; a model’s test claim is not regression proof.

Read the returned Run IDs with `oh-goon runs handoff RUN_ID --json`, `oh-goon runs steps RUN_ID` and `oh-goon runs tape RUN_ID`. Earlier findings, failures and candidate identities remain in their original Runs. Every phase uses the same FleetRun admission, claimed Run worker and existing Step/harness path; a campaign or native session is not another execution owner. These outcomes do not grant APP certification.

To choose a different reviewer roster, initialize a fresh workflow with explicit ordered authoring choices:

```sh
oh-goon init --template agents/agentic-review-repair-loop --kind plan --output ./custom-workflow --draft \
  --review authority=cookbook/single-source-of-truth-review \
  --review consumers=cookbook/canonical-consumer-contract-review
```

This writes an ordinary project-owned Composite at `goons/agents/review-repair/`, a Plan selecting it and a `run.yml` selecting that contained Plan. The Composite owns the ordered `phase: review` imports and retains the canonical adjudicate/repair/regression/verification edges. Initialization includes the local phase fixtures and the existing cookbook registry when selected. Custom review Skill resources remain caller-owned. `.goonskills.yml` remains the sole Skill-source registry; no cookbook Skill bodies are generated.

Edit the ordinary imports to replace, append or reorder reviews. Duplicate/missing aliases and invalid Skill references reject before publication. There is no runtime reviewer array or second scheduler. The entire draft is published exclusively as one directory. It remains NOT_READY without a canonical lock; complete its missing fields and prepare it explicitly.

## Native Codex service campaign

The [Hashmarks acceptance contract](../../docs/AGENT_SKILLS.md#hashmarks-dogfood-acceptance) defines separate qualification scenarios and source/evidence requirements. This recipe alone is not a live convergence receipt.

[`native-plan.yml`](native-plan.yml) explicitly selects local native review/adjudication/proposal Goons and the existing project check Goons. The native roles have read-only tools; the ordinary batch apply owner performs writes. Keep the Plan, `goons/`, registry and local Skill resources together under the workflow root.

For this distinct native workflow, initialize another draft directory, then copy
the native source files into it. Replace the checkout and repository paths:

```sh
oh-goon init --template agents/agentic-review-repair-loop --kind plan \
  --output ./native-workflow --draft --repository /absolute/path/to/registered-repository
cp /path/to/oh-goon/examples/agentic-review-repair-loop/native-plan.yml ./native-workflow/
cp /path/to/oh-goon/examples/agentic-review-repair-loop/native-run.yml ./native-workflow/run.yml
cp -R /path/to/oh-goon/examples/agentic-review-repair-loop/goons ./native-workflow/
```

The initialized registry and local phase Skills remain alongside those copied
Goons. Set the native selection's target/root/check paths. Configure a real
`codex_service` profile in the provider catalog and use its exact name as
`agent.profile` (the example `codex-service` is a placeholder). This selection
uses `agent.implementation: codex` and omits `agent.model_profile`; native
model/auth/settings remain native-owned. These local native Goons explicitly
select the Codex recipient; changing only the implementation field does not
make this campaign a Pi or OpenCode example.

Choose a complete supported public Codex installation outside private native state. Configure it through the [native service profile](../../docs/AGENT_PROVIDERS.md#native-service-adapter-design); do not copy or read personal auth/config stores. Native preflight and per-thread tool restrictions remain mandatory under that owner. Use the inspect/prepare/validate/run commands above with `./native-workflow/run.yml` as the single selection, including fresh `--authorize-repair` on execution.

Set `inputs.goal` to the task’s actual scope and phase contract: reviews apply only the selected Skill and report proven in-class findings; adjudication accounts for those findings; proposal supplies scoped repair data with behavioral regressions. The read-only phases never apply edits.

Keep the workflow and report outside the source checkout. The invocation's JSON
result includes the controller stop reason and structured campaign view.
`oh-goon runs campaign CAMPAIGN_OR_RUN_ID --json` reopens the committed intent,
linked Runs and controller decisions from the original Journal after restart.
The campaign ID also works when initial admission was refused and no Run exists.
The browser's campaign route uses the same durable read owner through
`GET /api/fleet/runs/campaigns/{campaign_id}`. Successful child verification without
a committed controller decision remains incomplete. Readback never resumes work
or authorizes PR publication.

For a controlled Hashmarks workload, start from a disposable public-source
export with an independent Git database. Exclude native project configuration
by path before reading or copying source: `.codex/`, `.opencode/`,
`opencode.json` and `opencode.jsonc`. Record the included source, exclusions and
original commit; the exported scope is the admitted review scope. Preserve the
original tree. If a full repository review exceeds the selected budget, declare a
new focused, runnable export before capture and establish its passing baseline.
Do not reduce an admitted scope after a finding or treat bounded orientation as
proof that the whole scope was reviewed.

The four selected Skills review architectural authority and consumer contracts.
A shared classifier that simply omits `?` is a correctness defect, but does not
by itself prove competing authority. Use an explicitly intentional architectural
mutation in the disposable candidate instead: leave
`hashmarks/paths.py::has_glob` correct and replace the `resolve_inputs` call to
that owner in `hashmarks/inputs.py` with a second, stale classifier such as
`if not any(ch in pattern for ch in "*["):`. The public `Glob("?.py")` contract
then accepts the canonical classification while input resolution treats the same
pattern as literal. Establish the original passing baseline and demonstrate that
the question-mark behavior now fails; Codex must still prove any finding from
source and public API evidence.

For this mutation, authorize `hashmarks/inputs.py` and
`tests/test_hardening.py` as write roots. Tailor `GOAL` to that input-classification
question while preserving phase roles: review applies only the selected Skill;
other reviewers may return CLEAN after completing their in-class review without
finding a defect. Adjudication accounts for the actual findings, and only the
proposal phase supplies repair data. Repository-relative write roots authorize
the later batch apply; they do not grant write tools to a reviewer or proposal
agent. Do not require a finding from every Skill or invent one to force a repair.

Both native check scripts should use Hashmarks' committed uv lock and run against
the declared export. Include task-evidence and post-change checks in final
verification when those contracts are part of that runnable source scope. Fresh
review obtains fresh Hashmarks evidence from the newly admitted candidate.
Hashmarks' same-repository `post_change` delta cannot replace that capture
boundary. This recipe defines qualification inputs; it does not establish that
a live campaign has converged.
