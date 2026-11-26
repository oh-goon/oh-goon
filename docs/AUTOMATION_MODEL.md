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

The [supervisor contract](development/EXECUTION_SUPERVISOR.md) owns the fresh
process/atomic-claim boundary and its remaining HUG-181/HUG-183 qualification.
Schedules remain triggers, and native
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

The [recurring Iris walkthrough](../examples/scheduled-iris/README.md) demonstrates
this public process/CLI/API path with the existing training project and retained
Run evidence. Its real wall-clock witness is distinct from bounded owner-clock
tests and from an explicitly requested manual occurrence.

### Recurring native reviews

With a managed Pi/Bubblewrap profile, seed one successful, read-only review on a
registered Git checkout. `--review` launches a headless Agent Run and selects an
ephemeral Pi session. A selected Skill and its reviewed resource files are captured
with the seed. Use one local state root for the seed, API server and readback.

```sh
oh-goon ai --agent pi --profile managed-review --workspace /absolute/repository \
  --config /private/providers.json --review --prompt-file /private/review.txt --json

oh-goon app serve --host 127.0.0.1 --port 8787 \
  --scheduled-agent-workspace /absolute/repository \
  --scheduled-agent-profile managed-review \
  --scheduled-agent-catalog /private/providers.json
```

Send an authenticated `POST /api/schedules/regular-review/from-review-run` with
the original Run ID from the seed result and an interval in minutes. Use the app
server's local access token in `X-Oh-Goon-Local-Access`.

```json
{"fleet_run_id": "SEED_RUN_ID", "name": "Regular repository review", "interval_minutes": 5}
```

The Schedule retains the seed Run ID, exact repository authority, Plan and
captured delivery. A successful occurrence follows the original Fleet lifecycle
and opens a fresh, bounded native session. Use `/api/schedules` and the original
Run and Game Tape readback to inspect each occurrence. Pause it in the Schedules
view or use the existing Schedule update API with its current generation and
record version. An admitted Run follows its own cancellation and settlement
path. Restart with the same selected runtime and local state to resume cadence.
The selected native Git boundary applies to native Plans. Ordinary Plans keep
their original Git and Run-worker selection on the same instance.

Recurrence is for the exact approved source and captured Skill. A changed source,
new task or new documents require a fresh seed and explicit Schedule selection.
Keep model credentials under the runtime's existing provider authority. The
Schedule reuses the retained selector and original recipient. The ordinary
`plan schedule` command accepts authored Plans with schedule-supported inputs.
See [sharing reviewed context and working on separate branches](AGENT_PROVIDERS.md#share-reviewed-context-and-continue-on-a-named-branch).

### Scheduled writable rounds

Start the app with the selected managed Pi profile and registered receiving
checkout using the same `--scheduled-agent-*` options. First publish a verified
candidate to the checkout's dedicated local branch using `runs advance-candidate`.
Use a published receipt that has yet to bind a successor. The Schedule reserves
that checkout, branch, and seed for the approved task list.

Send an authenticated `POST /api/schedules/agent-branch/from-branch-advance`
with the seed advancement ID, approved tasks in order, and interval in minutes.

```json
{
  "seed_advance_id": "approach-a-001",
  "interval_minutes": 5,
  "tasks": [
    {"task_id": "first", "prompt": "Implement the first approved change."},
    {"task_id": "second", "prompt": "Implement the second approved change."}
  ]
}
```

The first due occurrence selects the first task and uses the published seed.
After a successful round, the next due occurrence selects the next task and
advances the branch using the preceding verified candidate. The fresh Agent Run
uses the existing baseline and outgoing verifiers. The Schedule stores up to 32
approved tasks in order. Task prompts remain in private runtime state. Schedule
readback reports occurrences and their original Fleet Runs.

Each occurrence records its task and branch advancement ID before Git work. A
restart recovers the same pending publication or observes the same admitted Run.
Resume with the same scoped native runtime. While paused, the Schedule starts
no new writable Run. An already admitted child settles through Fleet. A
failed or unverified predecessor stops progression. The last task's verified
candidate remains available for explicit branch advancement if the Schedule has
no additional approved work.
[Local writable round setup](AGENT_PROVIDERS.md#advance-a-local-agent-branch-and-start-a-writable-round)

### Refill a writable Schedule from approved task docs

Bind one operator-controlled JSON document to the existing writable Schedule
through the authenticated local API. The generation ID comes from that Schedule
definition. Choose an absolute path outside its receiving checkout and bare
branch owner.

```http
PUT /api/schedules/agent-branch/approved-task-document
Content-Type: application/json
X-Oh-Goon-Local-Access: <local-access-token>

{"expected_generation_id":"<generation-id>","path":"/absolute/approved-tasks.json"}
```

Treat publication to that path as operator approval. Restrict write access to
the people allowed to approve writable work. Keep raw shared notes separately
and include selected, attributed excerpts in the approved prompts. Publish the
whole UTF-8 file by writing a temporary file in the same directory and renaming
it into place.

```json
{
  "tasks": [
    {"task_id": "follow-up", "prompt": "Apply the approved follow-up using the selected task note."}
  ]
}
```

A publication can contain up to 32 ordered tasks and be at most 2 MiB. After
the current list is exhausted and the next interval is due, the Schedule
captures the new tasks into private local state. The following ordinary tick
can admit the first captured task. A missing file, empty list, or an unchanged
publication leaves an exhausted Schedule waiting. A repeated ID with identical
task content is skipped. Reusing an ID for different content refuses that
publication. Use a new ID for a newly approved task.

Captured tasks remain ordered and immutable through restart. Each writable
round starts a fresh Pi session from the published verified commit. A paused
Schedule or unsuccessful predecessor prevents the next refill and Run.

### Move scheduled writable work to another host

Pause the source Schedule and let any admitted Run settle. Select a published
branch advancement with `runs branch-advance` and record its exact ref, commit,
parent, tree, and Git object format. Review the task documents approved for the
receiving agent. When the selected material is peer notes, export them with the
existing task-note command. Keep the full source receipt and runtime state on
the source host. [Select and export peer notes](AGENT_PROVIDERS.md#share-reviewed-context-and-continue-on-a-named-branch)

In an empty transfer directory, create a native bundle for the selected branch.
The receiving side needs the expected commit, parent, tree, and file hashes
from the approved handoff. Record the approved file hashes separately from the
transferred files. The full bundle includes the branch's reachable
history, so review that scope before transferring it. `branch.bundle` and the
explicitly selected `task-context.md` are the portable inputs in this example.

```sh
(
  set -eu
  test "$(git -C "$SOURCE_BARE" rev-parse "$REF^{commit}")" = "$COMMIT"
  git -C "$SOURCE_BARE" rev-parse --show-object-format
  git -C "$SOURCE_BARE" bundle create "$TRANSFER/branch.bundle" "$REF"
  test "$(git bundle list-heads "$TRANSFER/branch.bundle")" = "$COMMIT $REF"
  cd "$TRANSFER"
  sha256sum branch.bundle task-context.md > SHA256SUMS
)
```

On the receiving host, check the transferred hashes against the approved values
and check the bundle's advertised ref before importing it. Use a fresh bare Git
owner with the recorded object format, such as `sha1` or `sha256`. A full bundle
provides the Git objects for this branch without a remote.

```sh
(
  set -eu
  cd "$TRANSFER"
  printf '%s  %s\n' "$BUNDLE_SHA256" branch.bundle "$TASK_CONTEXT_SHA256" task-context.md \
    | sha256sum --check -
  test "$(git bundle list-heads branch.bundle)" = "$COMMIT $REF"
  test ! -e "$RECEIVING_BARE"
  git init --bare --object-format="$OBJECT_FORMAT" "$RECEIVING_BARE"
  git -C "$RECEIVING_BARE" bundle verify "$TRANSFER/branch.bundle"
  GIT_ALLOW_PROTOCOL=file GIT_NO_LAZY_FETCH=1 git -C "$RECEIVING_BARE" fetch \
    --no-tags --no-recurse-submodules --no-write-fetch-head \
    "$TRANSFER/branch.bundle" "$REF:$REF"
  test "$(git -C "$RECEIVING_BARE" rev-parse "$REF^{commit}")" = "$COMMIT"
  test "$(git -C "$RECEIVING_BARE" rev-parse "$COMMIT^1")" = "$PARENT"
  test "$(git -C "$RECEIVING_BARE" rev-parse "$COMMIT^{tree}")" = "$TREE"
)
```

Configure the receiving host with its own Oh-Goon database and League file,
local tools and managed Pi profile. Use those same state coordinates for the
CLI, the Schedule server, and restarts. Register two separate checkouts at the
transferred commit. One is the source for a local qualifying candidate Run.
The other is the detached receiving checkout used by the writable Schedule.

```sh
mkdir -p -m 700 "$RECEIVING_STATE"
export OH_GOON_DB="$RECEIVING_STATE/application.sqlite"
export OH_GOON_LEAGUE_FILE="$RECEIVING_STATE/league.yml"
GIT_ALLOW_PROTOCOL=file git clone --no-hardlinks --single-branch \
  --branch "${REF#refs/heads/}" "$RECEIVING_BARE" "$QUALIFYING_SOURCE"
GIT_ALLOW_PROTOCOL=file git clone --no-hardlinks --single-branch \
  --branch "${REF#refs/heads/}" "$RECEIVING_BARE" "$RECEIVING_CHECKOUT"
git -C "$RECEIVING_CHECKOUT" switch --detach "$COMMIT"
oh-goon league add-folder "$QUALIFYING_SOURCE"
oh-goon league add-folder "$RECEIVING_CHECKOUT"
```

Choose a small approved change and run it from the transferred commit with
`runs start-parallel` and the receiving host's independently selected frozen
verifier. Include approved task context in the task text. After the local
candidate passes, use `runs advance-candidate` to publish it from the transferred
tip and record the receiving host's own seed. Put approved excerpts from the
selected documents in the receiving Schedule's task prompts. Restart the
receiving app with the same local state and scoped runtime to reopen its
original occurrences. For the restart check, use a one-task test Schedule on
the receiving host. Record its occurrence and Run IDs before restarting.
After restart, check that its original Run has one `RUN_ADMITTED` event and
the Schedule has no extra occurrence. Keep the source Schedule paused while
the receiving Schedule owns this continuation. [Local branch commands](AGENT_PROVIDERS.md#advance-a-local-agent-branch-and-start-a-writable-round)

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
