# Shared Plans and Shared Runs

A **Shared Plan** is one ordinary Oh-Goon Plan saved in local Oh-Goon configuration and selected explicitly for a Shared Run. A Shared Run applies that one Plan unchanged across one or more explicitly selected repositories.

The core authoring rule does not change:

> Plans select and order Goons. Goons implement executable capabilities.

A Shared Plan is therefore a real `plan.yml`, never a renamed Goon and never a second workflow language.

## Source and scope are separate

Repository-owned CI/CD uses the repository's exact root `goon.yml` as its source and exactly that repository as its scope.

A Shared Run uses one saved Shared Plan as its source and the operator's explicit repository selection as its scope:

```text
Shared Plan
    ↓
selected repository A
selected repository B
selected repository C
```

For a Shared Run, repository `goon.yml` files are not read, validated, merged, inherited, replaced, modified, or used as fallback. The selected Shared Plan is resolved once and the same resolved Plan is applied to the admitted repository set.

## Local source of truth

Saved Shared Plans live outside repository roots and outside disposable operational SQLite state:

```text
~/.config/oh-goon/plans/<plan-key>/plan.yml
```

`OH_GOON_SHARED_PLAN_DIR` may explicitly select another local root.

`<plan-key>` is the explicit local catalog handle. Plan YAML contains no `name:` or `description:`; the selected key and original source digest remain separate facts. A shipped Plan such as `security/trivy-audit` can be saved under an operator-chosen local key without mutating its source bytes.

Writes are atomic and stale-write protected. Replacing an existing Shared Plan requires the exact SHA-256 revision that was read. The returned document is derived from the exact committed UTF-8 bytes; later reads reopen disk as authority.

## Plan templates, not Goon templates

`shared-plan from-template` copies a shipped **Plan template**. Plan templates live under:

```text
templates/plans/<category>/<name>/{plan.yml,README.md}
```

The copied `plan.yml` remains an ordinary editable Shared Plan. Its `goons:` roster imports canonical Goon packages from `templates/goons/`. Creating a Shared Plan does not execute anything.

Examples:

```bash
oh-goon shared-plan templates
oh-goon shared-plan from-template security/trivy-audit nightly-security
```

The explicit local key is required; Oh-Goon does not invent one from the template
path. Preview/run selections for saved Shared Plans remain UI/service operations.
Normal CLI execution uses `oh-goon run run.yml`; it does not retain the former
Shared Plan launch flags as an alternate configuration path.

## One execution engine

Source selection ends before execution:

```text
Shared Plan plan.yml
      ↓
FleetPlanService
      ↓
resolved Plan
      ↓
Enforcer / admission
      ↓
Fleet Run
      ↓
Events / outputs / receipts
      ↓
Game Tape
```

There is no separate Shared-Run executor, source merger, or hidden workflow engine.

Shared Plans also support explicit Agent selections through the same captured
Agent resolver used by the CLI. Select one registered Team, an acceptance
profile, existing absolute repository and Skill roots on the server, and either
an explicit model profile or a native implementation/profile. Recipient-free
read-only local-search needs no model connection. Shared Plan storage owns
only `plan.yml`; Skill registries/resources remain with the explicitly selected
Skill root. Native authentication and execution configuration retain their
existing owners.

Input inspection, lock preview and Team preview consume that selection. Starting
an Agent Run rechecks the exact saved revision, Plan identity, reviewed execution
digest and Team instance before storing source delivery or admitting work.
Phase-classified review-repair Plans require the separate bounded-repair action:
explicit review passes, write roots, campaign and Step deadlines, and fresh
authorization for that submission. It uses the existing campaign driver and
separately admitted Fleet Runs. Each phase retains the original Shared Plan
source fact. Editing the form invalidates its reviewed selection and repair
authorization.

The browser displays the selected campaign ID before submitting repair. A lost
HTTP response does not establish cancellation or failure: reopen that same ID
from Runs, including campaigns with zero admitted Runs. The campaign view reads
the original Journal and never resumes or retries work.
If cancellation or the deadline stops initial capture before durable intent is
recorded, the response reports an incomplete invocation with no Journal
revision. That observation is separate from a committed campaign decision.

## Lineups

A Lineup stores only a reusable explicit repository selection. It does not contain executable work, a Plan, scheduling state, or run state. Selecting a Lineup plus one Shared Plan still requires an explicit preview/run action.

## Durable evidence

A Shared Run records one `shared_plan` source fact containing the local Plan key and exact source SHA-256. Repository scope is recorded independently by the Fleet Run target set. Historical runs retain the exact source SHA they admitted; editing a saved Shared Plan never rewrites old evidence.
## Real preview and execution boundary

A Shared Plan defaults to local verification, like a file-backed Plan. Native UV-project requirements are supported in that profile. Strict CI/CD is selected explicitly with `execution: {purpose: cicd}`; its additional repository admission, isolation and retained-content requirements remain owned by that profile. Preview reports an incompatible resolved Plan as `FLEET_PLAN_INVALID` through the normal API error envelope.

Preview itself is non-effecting and does not require physical executor placement. A real Shared Run does: normal strict CI/CD admission requires an operator-configured `OH_GOON_EXECUTORS_FILE` captured when the Oh Goon process starts. The repository-owned `make strict-local-runtime-acceptance` journey is the narrow test-only exception: when no operator inventory is supplied, it materializes one temporary **real** Bubblewrap executor through the same tool, cgroup, and executor-schema authority used by strict execution so the production-shaped acceptance journey can exercise strict behavior without a fake host-execution fallback. APP `make local-runtime-verification` never uses that exception. Explicit strict-parallel/native acceptance still requires the operator-provided inventory and is not silently enabled by the temporary serial-runtime executor. Repository-owned `goon.yml` files remain omitted in both preview and execution.

