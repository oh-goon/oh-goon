# Review with a pinned agentsCookbook Skill

Classification: runnable-with-prerequisites

Run a typed read-only review using the existing `agents/native-review` Plan and the upstream `codebase-finding-derivation` Skill. Admitted repository evidence is disclosed to the selected native model recipient. A review result does not grant write authority or establish passing verification.

**Prerequisites:** an initialized Oh-Goon installation, registered workload repository, installed supported native agent and compatible provider-catalog profile. The checked-in selection uses Codex with an isolated managed Responses connection. Native sign-in, model settings, context window and compaction stay native-owned. Remote Git source capture may need network/cache access; ordinary inspection does not fetch it. The workflow directory must be separate from the workload source being reviewed.

Initialize a separate workflow, then copy the included `.goonskills.yml` into it:

```sh
oh-goon init --template agents/native-review --kind plan --output ./workflow --draft \
  --repository /approved/repositories/my-project
cp /path/to/oh-goon/examples/agent-skill-cookbook/.goonskills.yml ./workflow/
```

Run these commands from the intended workflow parent, replacing the absolute
paths. Draft initialization does not register a repository or create a lock.
Edit `workflow/run.yml` using [the complete selection](run.yml); its values are
example choices, not fallback configuration. The [registry](.goonskills.yml)
alone owns the remote source, pinned ref and selected Skill directories.

| Selection | Meaning and required choice |
| --- | --- |
| `target.repository` | Required registered workload directory. Relative paths resolve from `run.yml`'s parent; an absolute path makes the target explicit. |
| `inputs.repo` + `agent.repository_root` | Required captured source selection. `local:my-project` selects `my-project` beneath the absolute approved root `/approved/repositories`; it must identify the same directory as `target.repository`. It is not an internal repository ID. |
| `inputs.skill` | Required registry Skill reference. Select another registered Skill here rather than copying its source or body. |
| `agent.implementation` + `agent.profile` | Explicit native agent/profile pair. Use a real compatible profile from the existing provider catalog. These names do not configure authentication or a model. |
| `agent.acceptance` | Required acceptance selection; do not duplicate it in `inputs`. |
| `agent.skill_root` | Required workflow-contained Skill root. The example's `.` keeps the registry beside `run.yml`. |
| `inputs.goal` | Optional task override; omit it to retain the Goon's declared task. Inspection reports that default. |

The Plan/Goon owns the input types and defaults. `run.yml` supplies caller values
and execution selection; the provider catalog owns the selected connection.

```sh
oh-goon workflow inspect ./workflow/run.yml --json
oh-goon workflow prepare ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

`workflow prepare` captures the exact remote commit and Skill closure in `goon.lock`; normal execution reuses that identity. Inspection and validation are read-only and contact no model. A missing uncached remote source may require explicit acquisition before validation; preparation owns lock authoring. Changing the selected source/profile requires explicit new lock authority. No cookbook checkout is needed to build or test Oh-Goon.

Expected output is the native typed review record. Findings, incomplete scope, unavailable agent/provider and failed admission retain their real outcomes. Inspection/validation alone cannot prove a completed review. Use the returned Run ID for original durable readback:

```sh
oh-goon runs handoff RUN_ID --json
oh-goon runs outputs RUN_ID --json
oh-goon runs events RUN_ID --after 0 --json
```

Every invocation uses the existing FleetRun admission, claimed Run worker and
Step/harness path. Readback does not resume execution or grant new permissions.
See [Agent Skills](../../docs/AGENT_SKILLS.md) and [provider configuration](../../docs/AGENT_PROVIDERS.md).

## Headless Pi and the session boundary

For this initialized review, Pi is an explicit alternative: select
`agent.implementation: pi` and the exact compatible Pi `agent.profile` in the
same `run.yml`. Keep the registry, Skill and source selections unchanged.
Missing or unsupported profiles refuse rather than substituting another agent.

The public native CLI can also run a separate headless Pi task with that catalog:

```sh
oh-goon ai --agent pi --profile PI_PROFILE --config /absolute/path/to/providers.json \
  --workspace /approved/repositories/my-project \
  --prompt-file /absolute/path/to/task.txt --headless
oh-goon runs handoff RUN_ID --json
```

Replace `PI_PROFILE`, the files and the returned `RUN_ID`. Current headless Pi
execution is ephemeral (`--no-session`); no Oh-Goon session flag is needed.
`--handoff-from RUN_ID` seeds a newly admitted Run with recorded, untrusted
context. It does not reopen that Run's native Pi conversation or inherit writes.

There is currently no public Oh-Goon `--session new` or exact prior-Run session
reopen interface. The persistent journey in [HUG-19](https://linear.app/hugloss/issue/HUG-19)
and [HUG-53](https://linear.app/hugloss/issue/HUG-53) needs implementation and
installed-Pi proof; this example does not establish it. See the
[harness capability inventory](../../docs/development/AGENT_HARNESS_COMPATIBILITY.md)
for the actual qualification limits. An installed executable or a passing
protocol fixture does not establish model quality, native session continuity or
qualification on another host.
