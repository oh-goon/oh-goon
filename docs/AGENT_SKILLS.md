# Agent Skills

OH-Goon runs Agent Steps through the existing Plan → Goon → Step lifecycle.
The Resolver captures the selected repository and Skill resources, Fleet owns
admission and sequencing, the existing Step supervisor owns native processes,
and Game Tape records their outcomes.

Reusable review and development Skills are maintained in
[agentsCookbook](https://github.com/Hugloss/agentsCookbook). OH-Goon accepts
caller-selected Skill resources through its existing source contract. A cookbook
checkout is optional: the engine builds, tests, and runs without one. Skill text
does not grant tool access, writes, model recipients, or verification authority.

The current local model interface is deliberately bounded. It can inspect
captured files, propose one file replacement, and interpret a failed test's
declared diagnostic. A complete autonomous edit/test/repair loop with fresh
source capture between attempts is still open work.

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

Local model workloads additionally need `rg`, `head`, a running local Ollama
server, and an explicitly selected, already installed model. Source
initialization does not download Ollama or models. Model prompts are delivered
to the selected local recipient; a Skill file does not select another recipient
or grant shell, network, or repository-write capabilities.

## Current execution surface

| Capability | Current behavior | Boundary |
| --- | --- | --- |
| Skill source | `.goonskills` can identify local resources or an explicit Gitlink pin; captured resources travel with the frozen handoff. | Git-backed capture verifies the selected committed source. Capture does not install or update a submodule. |
| Local read-only tools | Selected harnesses can search fixed strings, list repository/Skill paths, and read admitted captured files. | Native tools operate on the attempt's frozen inputs; the model cannot supply arbitrary shell commands or read arbitrary host paths. |
| Bounded Skill loop | `local-model-skill-loop` permits up to four admitted tool actions followed by a final model evaluation. | Repeated or invalid requests and requests beyond the action budget fail the Step. |
| File proposal | `local-model-proposal` can return a bounded replacement bound to one captured file's source digest. | A proposal is data and does not modify the repository. |
| Explicit apply and test | A later operator-authored command Step can invoke `oh-goon agent apply` for the explicitly selected path, followed by an authored test Step. | The apply owner checks the proposed source identity; the native test's real result remains authoritative. |
| Failed-test feedback | An explicitly selected earlier command output can feed a later `local-model-skill-loop` Step. | The recipient remains read-only, and an earlier failed test keeps the Run failed. |

Ordinary local execution selects `plan run --standard-local --wait` and uses the
existing supervised host backend. This profile does not claim kernel sandbox
isolation. Its supported workload, process cleanup, and output contracts are
separate from optional high-security qualification. The
[execution-first decision](architecture/ADR_EXECUTION_FIRST_PROPORTIONAL_SECURITY.md)
sets that product direction.

Use the source CLI to inspect the exact options:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon plan run --help
uv run --offline --frozen --no-sync --project backend oh-goon lock --help
```

## Select a Skill

The [optional cookbook example](../examples/agent-skill-cookbook/README.md)
shows how to pin a real upstream Skill with native Git and select it through
`.goonskills` in your own workflow repository. OH-Goon verifies that committed
selection and captures its resources; it does not install a catalog or update
a submodule during a Run. The
[development-tool boundary](development/AGENTS_COOKBOOK_STRUCTURE_DOGFOOD.md)
links canonical review methodology for contributors working on OH-Goon itself.

The three local `SKILL.md` files shipped under `examples/` are small product
fixtures. They teach and test the source format; reusable review methodology
continues to be owned by agentsCookbook.

| Local example | Purpose |
| --- | --- |
| [Authoring](../examples/agent-skill-authoring/README.md) | Inspect named inputs and capture a local Skill; its `opencode` Goon is preview-only. |
| [Offline resources](../examples/agent-skill-offline/README.md) | Verify that a Skill, references, script resource, and asset survive source capture and relocation together. |
| [Local search](../examples/agent-skill-local-search/README.md) | Demonstrate frozen-source preparation for the original fixed-string search Step. |

Agent authoring binds the repository, Skill, and acceptance inputs explicitly.
The execution CLI uses `--agent-repository-root`, `--agent-skill-root`, and
`--allow-agent-acceptance`; normal resolution and `goon.lock` validation still
apply. The read-only `plan inputs --source-closure` command uses
`--repository-root`, `--skill-root`, and `--allow-acceptance` for source inspection.

## Failed-test feedback

The producer must be an earlier command Step with `on_failure: continue`, a
declared bounded output file, and one permitted attempt. The receiving
`local-model-skill-loop` Agent selects one exact
`${{ steps.test.outputs.diagnostic }}` reference and a matching
`FAILED`-only `step_status` condition. The original sequencer binds the actual
published scalar; the model receives it as escaped **UNTRUSTED DATA**.

Feedback must be nonempty and at most 2048 UTF-8 bytes. Missing or oversized
diagnostics are refused before the model launches; a successful producer skips
the failure-only Agent. Feedback does not replace the operator task, captured
source, selected model, or admitted tool set. It does not cause a retest, create
a fresh source revision, or turn a failed native test into success.

The current behavioral examples are the
[bounded Skill loop](../backend/tests/integration/test_agent01b3g_bounded_skill_loop.py),
[explicit proposal/apply/test sequence](../backend/tests/integration/test_agent01b3j_authorized_proposal_apply.py),
and [failed-test model feedback](../backend/tests/integration/test_agent01b3l_original_fleet_model_feedback.py)
integration tests. Their deterministic model fixtures verify protocol and
lifecycle behavior; they are not evidence of real model quality.

## Run native local-model qualification

With Ollama running locally and a model already installed, pass its exact name:

```bash
bash scripts/qualify_agent_local_model.sh YOUR_INSTALLED_MODEL
```

The script checks the selected local model, materializes the committed backend
dependencies through native uv, and exercises real Hashmarks, real Ollama,
the original Fleet Step supervisor, frozen Skill resources, and reopened
SQLite/Game Tape. Output goes under `.qualification/` by default. It does not
install a model and does not certify a complete repair loop or a release.

The script currently covers native read-only review and Skill-resource cycles.
Failed-test feedback and proposal/apply/test have separate deterministic
integration coverage. An unavailable model, dependency failure, skipped native
test, or rejected model response must not be reported as a native-model pass.
