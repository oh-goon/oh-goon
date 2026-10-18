# Local Lineups and Shared-Plan Preview

A **Lineup** is a small local preference that remembers a useful set of League Team roots. It is not a workflow, queue, scheduler, run target, or pipeline source.

```text
~/.config/oh-goon/lineups/backend/lineup.yml
        ↓
explicit Team root paths
        ↓ resolve on demand
current League Git identities
        ↓
Shared Plan preview or explicit run
```

## Why this exists

Shared Plans are most useful when the same quality/security playbook is applied repeatedly to a stable group of local repositories. Re-selecting twenty Teams on every launch is unnecessary friction, while persisting a second Team catalog or hidden run queue would create stale state and surprising execution.

Lineups keep only the user choice. The current League remains filesystem-derived.

## Lineup format

```yaml
version: 1
name: Backend teams
teams:
  - /home/me/code/api
  - /home/me/code/orders
  - /home/me/code/worker
```

Writes are atomic and local. Each committed `lineup.yml` has an exact-byte SHA-256 `revision`. Creating a new key uses no expected revision; replacing an existing Lineup requires the revision that was actually read. Compare + replace is serialized per Lineup key, so two browser/CLI writers cannot both commit from the same stale base. A successful save rereads the committed file and returns its new revision. A saved Team path must resolve to a managed Git Team when saved. On later use the exact saved root is checked again. If it moved, disappeared, or left the configured League, the Lineup reports `UNAVAILABLE`; oh-goon does not silently retarget it to another repository.

Repository-owned `goon.yml` is irrelevant to Lineup membership. The Lineup may contain Teams with configured, missing, or invalid repository pipeline files because a Shared Run uses only the explicitly selected saved `plan.yml`.

## Preview is the same Enforcer boundary

`shared-plan preview` resolves the selected Shared Plan `plan.yml` once and applies that exact immutable Plan to each selected Team through `FleetPlanService.preview_resolved()`, which calls the canonical read-only plan preview directly.

That means preview checks the same declared admission and repository requirements that Game Start will use. Preview is read-only and never starts a FleetRun.

```text
Shared Plan plan.yml
      ↓ resolve once
resolved Plan
      ├─ Team A → READY
      ├─ Team B → BLOCKED: Dockerfile missing
      └─ Team C → READY
```

A blocked preview does not mutate the Team, prepare a queue, or automatically start later. Run remains a separate explicit command/action.

## CLI

```bash
oh-goon lineup save backend \
  --name "Backend teams" \
  --team ~/code/api \
  --team ~/code/orders

oh-goon lineup inspect backend

oh-goon shared-plan preview python-quality --lineup backend
oh-goon shared-plan run python-quality --lineup backend
```

For one-off selections, use repeated `--team` instead. `--team` and `--lineup` are intentionally mutually exclusive so the effective selection is obvious.

## Sources of truth

- `league.yml` — saved League folders.
- filesystem — current Git Teams.
- `lineups/<key>/lineup.yml` — reusable user selections only.
- `plans/<key>/plan.yml` — reusable Shared Plan source.
- Journal/SQLite — actual run facts.

No Lineup is copied into a Team repository, and no Lineup changes the exclusive repository-Goon / `shared_plan` source rule.
