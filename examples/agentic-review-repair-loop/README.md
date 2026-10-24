# Agentic Review–Repair Loop

This example selects four pinned agentsCookbook reviews and two small local product fixtures. The shipped [Plan](../../templates/plans/agents/agentic-review-repair-loop/README.md) and [Composite Goon](../../templates/goons/agents/agentic-review-repair-loop/README.md) each remain two-file packages. No cookbook checkout is needed to build or test Oh-Goon.

Copy this example into your workflow directory. Its `.goonskills.yml` declares
the agentsCookbook repository once and selects four Skill directories. Native
Git prepares the source in Oh-Goon's managed cache; the first `lock` command
records the exact commit in `goon.lock`. Keep the registry, Plan, local resources
and generated lock together.

Normal runs reuse the recorded commit. A missing cache restores that same
commit. To refresh a branch or tag deliberately, repeat your lock command with
`--update-skills`. No submodule setup or committed Gitlink is required.

Configure an installed model or API endpoint once using [provider profiles](../../docs/AGENT_PROVIDERS.md):

```sh
oh-goon agent providers setup --provider ollama --model YOUR_INSTALLED_MODEL
```

Use `--provider litellm --model YOUR_MODEL_ALIAS` for a configured LiteLLM endpoint. Native model IDs may contain slashes. API keys stay in the environment named by that configuration. The profile's endpoint and model parameters become execution facts; key values never enter the lock or source packet.

Supply executable project checks, for example `scripts/regression` and `scripts/verify`. Their native exit status must be zero only when the intended checks pass. Include behavioral regressions for repaired defects in the admitted write scope. A model's regression claim is not proof that a test detects the defect; native checks and fresh review provide the observable evidence.

Set `PROJECTS` to the parent of the workload Git repository `my-project`, `WORKFLOW` to this separate workflow directory. First author and inspect the immutable execution lock:

```sh
oh-goon lock "$WORKFLOW/plan.yml" \
  --input repo=local:my-project \
  --input regression_command=scripts/regression \
  --input verification_command=scripts/verify \
  --agent-repository-root "$PROJECTS" --agent-skill-root "$WORKFLOW" \
  --allow-agent-acceptance agentic-review
```

Register `PROJECTS` with `oh-goon league add-folder` and get the workload's exact repository ID from `oh-goon league scan --json`. Then run:

```sh
oh-goon plan run "$WORKFLOW/plan.yml" \
  --repository-id REPOSITORY_ID \
  --input repo=local:my-project \
  --input regression_command=scripts/regression \
  --input verification_command=scripts/verify \
  --agent-repository-root "$PROJECTS" --agent-skill-root "$WORKFLOW" \
  --allow-agent-acceptance agentic-review \
  --review-passes 5 --repair-write-root src --repair-write-root tests \
  --repair-timeout 1800 --repair-step-timeout 300
```

Omit `--agent-model-profile` to use the configured default; pass it to both lock and run to override that selection. Ordinary execution runs on the host through the normal Fleet lifecycle and waits for completion. Select another JSON or TOML catalog with `--agent-providers-file` on both commands. It does not claim sandbox containment or APP certification.

Every review pass runs the four reviews in written order on one captured candidate. Findings do not skip later safe reviews. An incomplete execution or evidence record stops the phase. Findings lead to adjudication, proposal, native scoped apply and native regressions. The next pass starts at the first review. Five review passes allow at most four repairs. All reviewers must return accepted CLEAN records with exact scope accounting before final verification may run. Verification must pass on that same candidate.

The result lists the separate Run IDs. Inspect each using `oh-goon plan steps RUN_ID` or `oh-goon plan tape RUN_ID`. Earlier findings, failures and candidate identities remain in their original Runs. Interrupted or expired invocations do not silently resume writes; a new invocation needs a fresh lock and fresh limits.

To replace, append or reorder reviews, copy the small Composite Goon into your project's explicit local catalog and edit its ordered `phase: review` imports. Keep the other required phases. Any registered Skill can be selected with `with: {skill: your-alias}`; the controller does not recognize cookbook names. Unique unbound inputs are exposed automatically; `pass:` only routes deliberately shared names. The complete captured repository is the read scope, so the workflow never narrows scope to discard findings.

## Native Codex service campaign

The shared [Hashmarks dogfood acceptance contract](../../docs/AGENT_SKILLS.md#hashmarks-dogfood-acceptance)
defines the required source, configuration and execution evidence, the four
qualification scenarios, and the separate live Hashmarks repair milestone.

[`native-plan.yml`](native-plan.yml) selects the same four pinned cookbook
reviews and the existing regression/verification Goons. Its explicit local
catalog declares native review, adjudication and proposal roles. All three
roles have read-only tools; the ordinary batch apply command owns writes.
The service profile uses the native `codex` disclosure recipient.

Configure `codex_service` using the
[native service profile](../../docs/AGENT_PROVIDERS.md#native-service-adapter-design).
Select a complete public Codex 0.163.0 installation outside native state. Native
Codex retains its existing sign-in and configuration; neither this example nor
Oh-Goon opens or copies those stores. The selected home must support the
documented capability preflight. Existing MCP registrations may remain in that
home: native per-thread overrides disable their workload tools before model work.
Metadata discovery may initialize the registered servers under ordinary native
startup semantics; it does not grant those tools access to the campaign.

Use the same native selection for lock and run. For example, with `PROVIDERS`
pointing to your Oh-Goon provider catalog and `PROFILE` to its service profile:

```sh
GOAL='In review phases apply the selected Skill to the complete declared scope and report only proven findings in its class. In adjudication account for supplied findings. In proposal phases provide scoped repair data with behavioral regressions. Read-only reasoning never applies edits.'

oh-goon lock "$WORKFLOW/native-plan.yml" \
  --input repo=local:my-project \
  --input "goal=$GOAL" \
  --input regression_command=scripts/regression \
  --input verification_command=scripts/verify \
  --agent-repository-root "$PROJECTS" --agent-skill-root "$WORKFLOW" \
  --allow-agent-acceptance agentic-review \
  --native-agent codex --native-profile "$PROFILE" \
  --native-workspace "$PROJECTS/my-project" --agent-providers-file "$PROVIDERS"

oh-goon plan run "$WORKFLOW/native-plan.yml" \
  --repository-id REPOSITORY_ID \
  --input repo=local:my-project \
  --input "goal=$GOAL" \
  --input regression_command=scripts/regression \
  --input verification_command=scripts/verify \
  --agent-repository-root "$PROJECTS" --agent-skill-root "$WORKFLOW" \
  --allow-agent-acceptance agentic-review \
  --native-agent codex --native-profile "$PROFILE" \
  --native-workspace "$PROJECTS/my-project" --agent-providers-file "$PROVIDERS" \
  --review-passes 3 --repair-write-root src --repair-write-root tests \
  --repair-timeout 1800 --repair-step-timeout 300 --json > campaign-result.json
```

Keep the workflow and report outside the source checkout. Save the invocation's
JSON result: it includes the actual controller stop reason and structured
campaign view. `oh-goon plan campaign RUN_ID --json` reads retained linked Runs;
its controller result remains unavailable because the final stop receipt is
invocation-only. Neither report authorizes continuation or PR publication.

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
