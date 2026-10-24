# Local Team run contract

The normal local CI/CD action is **Run Team**. This is **Team Goon mode**: a Team is one Git repository discovered beneath a saved League folder, the root plan source is exactly that Team's `<git-root>/goon.yml`, and the execution scope is exactly that one Team.

## Team Goon: one repository-owned `goon.yml`, one Team

```text
League map / CLI / CI-style plan invocation
  -> repository-root goon.yml
  -> FleetPlanService resolves the immutable plan
  -> FleetPlanService previews it or performs the single FleetRun admission
  -> normal FleetRun lifecycle
  -> normal repository-effect / Fleet-plan execution
  -> Journal + Enforcer + Hockey evidence
```

`GoonService` is intentionally only the repository entrypoint adapter: it proves the requested path belongs to a managed Team, chooses exactly `<git-root>/goon.yml`, and delegates to the canonical Fleet-plan service. The file bytes used by resolution must match the Team source digest supplied for admission; source drift fails instead of silently running a different revision. The durable Team source stores only that digest: the owning Team is derived from the one admitted repository target, so source metadata cannot disagree with a second stored repository ID. It does not know `FleetRunControlPlane`, does not construct `OperationAction.EXECUTE_PLAN`, and does not implement admission policy.

There is therefore no "local execution path" in the backend. **Local** describes how the user starts and observes the run; execution semantics are the same CI/CD semantics used by the resolved plan. The browser, API and CLI all enter the same durable FleetRun boundary.


## Inspection uses the same file too

`oh-goon team validate` and `oh-goon enforcer run` do not apply a separate local baseline. They resolve the Team's current root `goon.yml` and call the same read-only Fleet-plan preview/admission owner used immediately before execution. A requirement belongs in `goon.yml`; local repository inspection must not rediscover or invent it from project type.

The League Map itself remains a cheap structural discovery view: it tells you whether the exact root `goon.yml` is present/safe to read and fingerprints its bytes. Semantic CI/CD readiness is established only by explicit inspection or run admission, so scanning a large League never executes or silently validates every pipeline.

## Generic CI/CD Teams

Fleet-plan target selection uses the live League Team map, not the older Python-maintenance catalog. A Git Team does not need `pyproject.toml` merely to be eligible for CI/CD. Repository-specific requirements belong in its resolved `goon.yml` admission contract.

This does not weaken strict admission: a `goon.yml` may require `pyproject.toml`, `uv.lock`, `Dockerfile`, tools, policies or any other supported preflight fact, and the Enforcer still rejects the run before `RUN_ADMITTED` when those requirements fail.

## Foreground CLI

```bash
oh-goon run /path/to/workflow/run.yml
```

selects `source: {kind: repository-goon}` and the explicit registered target.
It is intentionally attached. It resolves the Team's current `goon.yml`, starts one durable FleetRun, waits for that run to become terminal in the local Journal, prints the final status and returns a non-zero exit code unless the run completed successfully.

The CLI does not start a second executor or interpret stdout to decide success. It observes the canonical FleetRun terminal state.

The browser/API remain asynchronous because the local application process stays alive and the existing FleetRun watcher projects progress into the UI.

## No hidden Team state

Only League folders are persisted as user preferences. Team existence, repository identity and `goon.yml` state remain filesystem-derived. Running a Team does not add a `teams` table, recent-team registry or copied plan file.

The plan is resolved again when the user presses Run. A prior League scan is presentation evidence, not execution authority.

## Fast local path lookup

A foreground Team command does not rescan the whole League. The requested path is resolved against the saved League roots and bound to the same Git-root Team identity used by League discovery, then only that Team's root `goon.yml` is inspected. Incidental ancestor directory names never hide the Team; targeted lookup and League discovery must agree on Git-root semantics.

Normal CLI execution supplies a `run.yml` whose source is
`{kind: repository-goon}` and whose target is the registered repository ID.
It does not infer execution scope from the current directory or a path alias.
Read-only `team validate` may still inspect an explicit path or current directory.
The resulting Run uses the canonical Git-root identity.

## Foreground interruption and local serialization

The CLI prints the durable Fleet Run ID as soon as admission succeeds. `Ctrl+C` requests cancellation through the normal FleetRun lifecycle, waits for the durable terminal cancellation fact, and exits with code `130`; it does not abandon a private local worker or invent a second cancellation path.

There is no hidden local queue. Local execution remains intentionally serialized. If another Fleet execution owns the lease, a second Team start fails clearly with `ExecutionBusy`. oh-goon does not silently queue a local Team run behind it. The user decides what should run next.

When a foreground run fails, the CLI prints the canonical failure code/stage/message and the existing `oh-goon dev explain-run <id>` inspection command. The browser marks the one active Team as **On ice** and refreshes the League once at terminal completion so any repository or root-`goon.yml` changes become visible without repeated background scanning.
