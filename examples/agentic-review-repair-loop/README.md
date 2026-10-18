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
