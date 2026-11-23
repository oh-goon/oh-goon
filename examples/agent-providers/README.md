# Agent provider connections

Classification: configuration-example

`oh-goon ai` selects Codex by default; configure its existing sign-in and
`default_native_profile` using the [native profile example](../../docs/AGENT_PROVIDERS.md#use-an-existing-codex-sign-in).
This sample catalog contains HTTP provider connections and named model profiles.
It declares neither `default_model` nor native execution profiles.

Copy [providers.toml](providers.toml) to the path printed by `oh-goon agent providers path`, or select it directly with `--config`. Replace model placeholders with IDs from your provider. This example needs no provider server, credentials or provider SDK to validate:

```sh
oh-goon agent providers validate --config examples/agent-providers/providers.toml
oh-goon agent providers list --config examples/agent-providers/providers.toml --json
```

When a selected endpoint is available, send explicit text:

```sh
oh-goon agent providers request --config examples/agent-providers/providers.toml \
  --model router --prompt "Reply with hello."
```

The selected model profile determines which credential environment variables are
required. Configure only the providers you use; `default_model` is optional when
`--model` selects an existing entry. Here `router` names `models.router`, which
references `providers.openrouter`; it is not a native `ai --profile` selection.
Ollama and LiteLLM run separately and are never installed, launched or searched
for by these commands.

See [Agent provider configuration](../../docs/AGENT_PROVIDERS.md) for configuration, native parameters, custom gateways, Python usage and failure behavior. Provider catalog inspection proves configuration only. Endpoint availability, compatible native profiles and admitted Agent execution remain separate states; normal workflows select their profile in `run.yml`.

## Reopen a managed Pi session

Choose an existing compatible model profile in your provider catalog. Replace
the paths with your installed public Pi executable and runtime, the exact Git
repository, and a Skill directory containing `SKILL.md`. Select the catalog
once with `OH_GOON_PROVIDERS_FILE` so both `ai` and initialized workflows use
it. If the catalog already selects a Pi executable and runtime, omit
`--executable` and `--runtime-root` to reuse that selection.

```sh
export OH_GOON_PROVIDERS_FILE=/absolute/path/to/providers.json
oh-goon agent setup pi --mode managed --profile pi-rpc --pi-rpc \
  --inference-profile approved-code --executable /public/pi/bin/pi \
  --runtime-root /public/pi --workspace-root /absolute/path/to/repository --no-input
oh-goon ai --agent pi --profile pi-rpc --workspace /absolute/path/to/repository \
  --skill /absolute/path/to/skill --session new --prompt-file task.txt --verify ./verify --json
```

Use the printed `fleet_run_id` for `ORIGINAL_RUN`. After the first invocation
exits, start a new invocation with a new task. `./verify` is your repository's
independent executable check.

```sh
oh-goon ai --agent pi --profile pi-rpc --workspace /absolute/path/to/repository \
  --skill /absolute/path/to/skill --session run:ORIGINAL_RUN \
  --prompt-file next-task.txt --verify ./verify --json
oh-goon runs handoff NEW_RUN --json
oh-goon runs outputs NEW_RUN --json
oh-goon runs events NEW_RUN --after 0 --json
oh-goon runs tape NEW_RUN --json
```

The continued Run gets a new ID. The recorded Pi session evidence identifies
the prior Run and observed native leaf, and the original Steps record
cleanup and verification. These commands read the Journal without launching
Pi. Use `--handoff-from ORIGINAL_RUN` with another selected harness to send
bounded prior evidence to a fresh native session.

### Use the same selection in a Plan and Goon

To continue through an initialized workflow instead of the second `ai`
command, start with a draft and add a project-owned Goon. Use the latest
qualified writer ID in its Step. The shipped writable coding template
remains available for ordinary native coding.

```sh
oh-goon init --template agents/native-coding --kind plan --output ./resume --draft
```

Create `resume/goons/agents/resume-pi/goon.yml` and a companion `README.md`
with the heading `# agents/resume-pi`.

```yaml
inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  goal:
  acceptance: {type: acceptance_profile, default: native-coding}
steps:
- uses: agent
  harness: native
  role: report
  task: ${{ inputs.goal }}
  session: {mode: continue, prior_run_id: ORIGINAL_RUN}
  requested_effects: {repository_access: read_only, recipients: [configured_model]}
```

Set `resume/plan.yml` to import the Goon and the existing verifier.

```yaml
goons:
- import: agents/resume-pi
  from: goons
- agents/verification
```

Use the selected repository under `repository_root` and place the selected
Skill under `resume/skills/inspect`. Complete the initialized `resume/run.yml`.

```yaml
version: 2
source: {kind: file-plan, path: plan.yml}
target: {repository: /approved/repositories/project}
inputs:
  repo: local:project
  skill: local:inspect
  goal: Inspect the current checkout using context from the saved session.
  verification_command: verify
agent:
  implementation: pi
  profile: pi-rpc
  acceptance: native-coding
  repository_root: /approved/repositories
  skill_root: skills
```

```sh
oh-goon workflow prepare ./resume/run.yml --no-input
oh-goon run ./resume/run.yml --no-input --json
```

Both paths use the same claimed Run worker and typed session selection. Use
one continuation per predecessor. To continue again, select the new
qualified writer and prepare after changing the Goon. Persistent sessions
require a directly selected repository Run. The Shared Plan and Schedule
surfaces refuse these selections.
