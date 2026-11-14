# Recurring Iris training

Classification: runnable-with-prerequisites

This trusted-local example uses the existing
Schedule clock and the original Fleet Run/Step/artifact owners. It reuses the
complete [Iris project and single-command Plan](../../templates/plans/ml/iris-training/README.md);
it introduces no scheduler Step or model registry.

## Select the workload once

Use an initialized source-checkout CLI with Python 3.12+, native Git and uv.
Start from the Oh-Goon checkout and copy the actual project to a new location
whose parent exists:

```sh
iris_repository=/absolute/path/to/iris-application
iris_workflow=/absolute/path/to/iris-schedule
cp -R templates/goons/ml/iris-train "$iris_repository"
git -C "$iris_repository" init -q
oh-goon init --template ml/iris-training --kind plan --output "$iris_workflow" --repository "$iris_repository" --no-input
oh-goon plan schedule "$iris_workflow/run.yml" --schedule-id iris-demo --interval-minutes 1
```

The one-minute interval makes the demonstration observable. Choose a suitable
interval for ongoing use. `init` provides the actual registered target and first
lock; the Plan exposes the declaring Goon's inputs without redefining them.
Creation captures the selected Plan, inputs and Python project for future
occurrences. It does not start training or launch a background product process.

## Keep the product process running

In a terminal using the same installation and state configuration:

```sh
oh-goon app serve --host 127.0.0.1 --port 8011
```

Leave this foreground process running. Starting it is not a trigger. A new
Schedule first becomes eligible one full interval after creation. Later
eligibility is anchored to the previous occurrence's terminal `finished_at`,
not its start time. Training duration and the clock's polling delay therefore
add to the observed time between starts. An active occurrence does not overlap
the next automatic occurrence of that Schedule.

The process prints its local API token. Treat it as a credential; keep logs and
API snapshots private. In another terminal at the Oh-Goon checkout, create a
private directory outside the selected project/workflow:

```sh
umask 077
task_schedule_private="$(mktemp -d)"
```

Use your editor to create `$task_schedule_private/header` containing one line:
`X-Oh-Goon-Local-Access: TOKEN_FROM_THE_RUNNING_PROCESS`. This keeps the token out
of shell history and command argument values. On a process restart, use that
process's token.

## Observe each original occurrence

```sh
curl --fail --silent --show-error --header @"$task_schedule_private/header" http://127.0.0.1:8011/api/schedules --output "$task_schedule_private/catalog.json"
```

Find the `schedules` entry whose `definition.schedule_id` is `iris-demo`.
`next_run_at` reports eligibility; it is not a promised start or a completed Run.
`missing_expected_occurrence_at` reports overdue work without inventing a Run.
Once present, save `latest_occurrence.schedule_run_id` before observing the next
occurrence: this catalog is a latest-state view, not an occurrence-history API.

Read that occurrence's repository page using its actual ID:

```sh
schedule_run_id=ACTUAL_OCCURRENCE_ID
curl --fail --silent --show-error --header @"$task_schedule_private/header" "http://127.0.0.1:8011/api/schedules/iris-demo/occurrences/$schedule_run_id/repositories" --output "$task_schedule_private/occurrence.json"
```

Each admitted `items[].fleet_run_id` names the original canonical Run. A pending
or not-started target cannot supply an admitted Run ID. Use the original evidence:

```sh
oh-goon runs tape ACTUAL_FLEET_RUN_ID --json
oh-goon runs outputs ACTUAL_FLEET_RUN_ID --json
mkdir -p ../iris-schedule-results
oh-goon runs export-artifact ACTUAL_FLEET_RUN_ID --step ACTUAL_STEP_ID --path ml-output/iris-training-metrics.json --output ../iris-schedule-results/occurrence-1-metrics.json
oh-goon runs export-artifact ACTUAL_FLEET_RUN_ID --step ACTUAL_STEP_ID --path ml-output/iris-model-parameters.json --output ../iris-schedule-results/occurrence-1-model.json
```

Use new destinations for the next occurrence. Logical Step names can repeat;
the Run/repository/Step/attempt context identifies each execution. Retained
objects may deduplicate identical model bytes without merging Run histories.
Availability remains an artifact fact; a finished occurrence alone is not proof
that its bytes can still be retrieved.

## Pause or change the definition explicitly

The existing public API supports versioned updates. From a fresh catalog
snapshot, build a pause request without reconstructing the execution target:

```sh
curl --fail --silent --show-error --header @"$task_schedule_private/header" http://127.0.0.1:8011/api/schedules --output "$task_schedule_private/catalog.json"
uv run --project backend --offline --frozen --no-sync python - "$task_schedule_private/catalog.json" "$task_schedule_private/pause.json" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    entries = json.load(source)["schedules"]
definition = next(row["definition"] for row in entries if row["definition"]["schedule_id"] == "iris-demo")
fields = ("name", "repository_ids", "repository_instance_ids", "target", "interval_minutes", "state", "generation_id", "record_version")
request = {key: definition[key] for key in fields}
request["state"] = "paused"
with open(sys.argv[2], "x") as destination:
    json.dump(request, destination)
PY
curl --fail --silent --show-error --header @"$task_schedule_private/header" --header 'Content-Type: application/json' --request PUT --data-binary @"$task_schedule_private/pause.json" http://127.0.0.1:8011/api/schedules/iris-demo --output "$task_schedule_private/paused.json"
```

Read a fresh catalog before every update; a stale generation/version is refused.
Pausing stops automatic eligibility, not an already admitted Run. An explicitly
requested manual occurrence is a separate operation. To resume, build a fresh
request with `state: active`. An overdue resumed definition can become eligible
once immediately; it does not gain a new initial interval or a catch-up queue.
Use a new request filename, such as `resume.json`, for each explicit update.

To select different inputs or captured source, edit the workflow, prepare it,
then explicitly replace the Schedule through its existing CLI owner:

```sh
oh-goon workflow prepare "$iris_workflow/run.yml" --no-input
oh-goon plan reschedule "$iris_workflow/run.yml" --schedule-id iris-demo --interval-minutes 1
```

`reschedule` selects an active replacement. It retains the existing timing
anchor, so overdue work can become eligible on the next tick. It does not keep
a previously paused definition paused. Use a fresh API definition to pause
again when intended; that cannot undo a Run already admitted.

For example, explicitly select `minimum_accuracy_percent: 100` to observe a
failed training occurrence. The simple Iris command can retain measured
diagnostic metrics/parameters after failure; those bytes do not turn the Run
into a successful training result. Always use its original outcome and Step
evidence. Editing a caller file or moving a Git ref alone does not replace the
Schedule's frozen selection or refresh its pins.

## Cancel an admitted training Run

Pause the Schedule first when you also want to prevent later automatic work.
Then use the admitted `fleet_run_id` from the occurrence's repository page and
send cancellation to the same foreground application that admitted it:

```sh
fleet_run_id=ACTUAL_FLEET_RUN_ID
curl --fail --silent --show-error --header @"$task_schedule_private/header" --request POST "http://127.0.0.1:8011/api/fleet/runs/$fleet_run_id/cancel" --output "$task_schedule_private/cancellation.json"
oh-goon runs tape "$fleet_run_id" --json
```

Read the occurrence page again to see its original child Run settle. A pause
does not cancel admitted work, and requesting cancellation is not proof that
native work has stopped. Use the terminal Run/Step and cleanup evidence;
unresolved cleanup remains unresolved. Diagnostic metrics retained before
cancellation do not make the training successful. This uses the running
application's existing Run control; it does not attach another CLI execution
owner or create a replacement occurrence.

## Qualification boundary

The [native example proof](../../tests/integration/test_scheduled_iris_example.py)
uses the actual foreground process and public CLI/API. The real wall-clock
recurrence and automatic failed-training variants have daily cadence; ordinary
feedback covers creation, first-interval observation, public pause control,
an API-started failed occurrence and cancellation of live native training.
The manual failure verifies failure/readback semantics, not automatic recurrence.
The separate automatic
failure waits for the full initial interval and preserves its non-successful
native training outcome. Existing bounded Schedule owner tests cover timing
and admission races without pretending their controlled clocks are a
public-process wall-clock proof. SQLite reopen cases preserve the original due
anchor after multiple missed intervals. The cancellation fixture repeats the
native Iris command finitely to provide an observable training window; it
requires a completed fit, a live training process, cancellation through the
original application and native process-session quiescence.

There is no cron/catch-up promise or automatic process startup. With no product
process running, no clock is evaluated; on restart, the existing owner observes
the saved definition and latest terminal occurrence. Strict custody, arbitrary
detached-process containment, multiple missed intervals on other hosts, private Git,
installed-release and WSL/container qualification require their own evidence.
Stop the foreground process with its normal terminal interrupt after pausing
the demonstration. Schedule operator CLI expansion remains HUG-128.
