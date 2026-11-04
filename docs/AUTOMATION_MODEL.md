# Repositories, Shared Runs, CI/CD, Schedules, and Game Tape

Oh-Goon has several automation entry points, but only one execution architecture.

```text
Trigger
  ↓
Plan
  ↓ selects and orders
Goons
  ↓
Steps
  ↓
native tools / commands
```

Once a run is admitted, its trigger does not create a different executor, event model, log path, output path, Enforcer path, history model, or Game Tape implementation.

Manual local, CI/CD, scheduled, ML and application work, including Agent Steps,
enters the same FleetRun admission and execution owners. The required
[Run-worker custody boundary](development/OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md)
fits beneath that admission:

```text
FleetRun admission → ExecutionTicket → RunExecutionSupervisor
  → claimed isolated Run worker → FleetRunExecution → ExecutionSession
  → FleetTargetScheduler → Step/Attempt → native tool or harness
```

This is the target of HUG-180/HUG-181. The current supervisor submits the
existing Run execution to a thread pool; that implementation is not a second
supported mode to preserve after cutover. Schedules remain triggers, and native
sessions remain below Step/Attempt. A worker claim transfers custody only;
it cannot resolve another Plan, select repository work or reserve capacity
again. SQLite/Journal and the Fleet lifecycle retain durable Run truth.

## Concept ownership

| Concept | Repository scope | Plan ownership | Trigger | Executable work |
| --- | --- | --- | --- | --- |
| Shared Run | one or more explicitly selected repositories | one explicitly selected shared Plan | operator/manual | no |
| Repository CI/CD | exactly one repository | repository CI/CD Plan | Git/source change | no |
| Schedule | one or more explicit repositories | one resolved Plan | wall-clock time | no |
| Plan | none | orders Goons | none | no direct Steps |
| Goon | none | capability implementation | none | yes, through Steps |
| Game Tape | observes a Run | observes resolved execution | run evidence | no |

The important separation is:

> **Shared Runs and Schedules may both target many repositories; the distinction is the trigger. Shared Runs are explicit operator runs, Schedules are time-triggered occurrences.**

A Shared Run executes one explicitly selected Plan over its explicit repository set. It does not read, merge, or inherit those repositories' CI/CD or Schedule Plans.

A Schedule definition selects an explicit repository target set and one resolved Plan. It determines **when** that Plan may start for those targets. The Occurrence groups context only; each repository keeps independent canonical Run truth. It never contains executable recipe work.

## Shared Runs remain first-class

```text
Shared Plan
   ↓
repo-a
repo-b
repo-c
```

Shared Plan UI/service operations retain explicit multi-repository scope. Normal
CLI execution selects one registered target in `run.yml` and uses `oh-goon run
run.yml`. Neither an empty target nor a Schedule implies an all-repository Run.
Shared Runs remain owned by Fleet and are not reimplemented through the scheduler.

## Repository Schedules

```text
wall-clock time
      ↓
Schedule definition
      ↓
explicit repository targets + one resolved Plan
      ↓
normal Fleet admission for each target
      ↓
same execution path → Journal → Game Tape projection
```

Create Schedules through the existing UI/API owner, or select the same `run.yml`
with the original Schedule CLI:

```sh
oh-goon plan schedule ./workflow/run.yml --schedule-id nightly-security --interval-minutes 1440
```

`plan reschedule` selects the replacement config explicitly. The selected target,
reviewed Plan identity and wall-clock interval remain Schedule-owned; ordinary
`oh-goon run run.yml` does not create or mutate a Schedule.

A Schedule is active or paused and uses an interval trigger. A newly created Schedule waits one full interval before becoming due; later recurrence is anchored to the previous terminal execution's `finished_at`. Starting the Oh-Goon process is not a trigger.

The public API uses `/api/schedules`. The older generic browser workflow composer and `/api/workflows` product route are not part of the product model.

## Schedule definitions are not workflow programs

Valid:

```text
Schedule
  ↓ selects
Schedule Plan
  ↓
Goons
  ↓
Steps
```

Invalid:

```text
Schedule
  ↓
shell / recipe / arbitrary scheduler steps
```

The invalid form duplicates the Plan/Goon/Step execution language. An explicit
repository target set is valid: each target becomes its own FleetRun through
the original admission owner. The Schedule occurrence groups observations; it
does not execute the targets or create a Schedule worker, queue or replay loop.

## CI/CD and Schedule state are different

CI/CD answers whether a code revision passed the repository's change-validation requirements. Scheduled automation answers whether a time-dependent or external fact has changed, such as a newly published CVE.

A later Schedule finding must not rewrite an earlier CI/CD result. Both executions nevertheless use the same run/debug evidence once started.

## Game Tape is universal run inspection

Every admitted run should converge on the same inspection surfaces:

```text
Overview | Timeline | Steps | Logs | Outputs | Enforcer
```

The product distinction is:

- **Schedules** are future/planning state: what is configured to start and when.
- **Game Tape** projects recorded execution evidence: what happened, when, for how long, and why it passed/failed/was blocked. It cannot settle or recover a Run from logs, worker exit or native-session status.

The intended visual language is compatible without conflating the concepts: future wall-clock time can be the X-axis in the Schedules view, while actual elapsed execution time is the X-axis in Game Tape.

## Durable Schedule ownership

Schedule semantics and Schedule persistence now use the same vocabulary. There is no retained generic `Workflow*` model/store layer and no `workflow_*` SQLite execution path.

The durable boundary is intentionally small:

1. `ScheduleDefinitionStore` persists the canonical repository target set, one resolved Plan target, interval, generation/version, and active/paused state;
2. `ScheduleAdmissionPlanner` captures the exact definition plus Runtime Safety Cut and binds one immutable `ScheduledFleetRunIntent`;
3. `ScheduleExecutionStore` persists one structural Schedule run with that intent and exact attention/failure evidence;
4. each target's canonical Fleet Run ID is derived from `schedule_run_id` and its repository ID;
5. `ScheduleExecutionEngine` admits or observes those independent Fleet Runs through `FleetRunControlPlane` and settles the occurrence from their canonical evidence;
6. there is no `steps[]`, `step_index`, cursor, scheduler recipe language, or second execution graph;
7. Shared Runs retain operator-selected scope, while Schedules retain an explicit target set and wall-clock trigger; neither introduces another execution lifecycle.

Do not reintroduce `Workflow*` storage or APIs as compatibility vocabulary. A Schedule decides when its explicit repository targets run one Plan; each target enters Fleet/Plan/Goon/Step as an independent Run and Game Tape remains the universal execution/debugging surface.
