# Startup simplification phases

Oh-Goon now has two deliberately different frontdoors.

## Development

```text
make test
  -> dependency-light shell runner
  -> prepare backend and frontend independently
  -> run independent code checks even after ordinary failures
  -> aggregate PASS / FAIL / BLOCKED / SKIPPED
```

Development startup must not require the application to already possess a strict
production executor. It also must not put formatting, lint, browser installation or
release certification in front of runnable code tests.

## Certification

```text
make certify
  -> require the canonical initialized backend runner
  -> one canonical static admission in the certification frontdoor
  -> isolated certification control-state lease
  -> backend bootstrap-certification capability
  -> league add-folder
  -> lock the canonical APP verification Plan
  -> run four semantic APP Goons against the initialized repository
  -> wait for terminal evidence
```

The certification path remains orchestration only. It must not become a second
implementation of application behavior.

Certification also follows the fail-before-work/anti-cucumber contract in `FAIL_BEFORE_WORK.md`: knowable invalid authority is rejected by its earliest legitimate owner before workload begins, while source-shape/maintainability proxies and runtime-only predictions do not become startup gates.

### Native materialization and local controller have separate authority

Canonical `make init` is the only normal local dependency-materialization boundary. APP certification never calls it internally and never creates a second backend/frontend dependency environment. Missing initialization fails before APP workload with explicit `make init` guidance.

`make certify` starts the exact initialized checkout controller:

```text
backend/.venv/bin/oh-goon-workflow
```

Dependency materialization remains owned by the same native repository workflow used by development: `make init`, native `uv sync --locked`, and native `npm ci`. There is no certification-specific dependency universe, retained package-manager supply, shadow resolver, or mandatory certification-setup path. Native uv/npm own their caches, registry behavior, dependency resolution/materialization, and environment semantics.

APP verification consumes already-established execution authority directly. Strict executor/cgroup delegation remains owned by release/CI paths that actually create strict workload attempts; it is not a prerequisite for APP verification. Neither path may reconstruct project dependency semantics or require callers to restate ordinary repository-native tools at every Goon/Step. Ambient `python`/`python3` or `PYTHONPATH` bootstraps remain forbidden because project Python ownership is `pyproject.toml` + uv.

### backend owns certification process authority

The workflow calls the state-free backend `bootstrap-certification` capability for
repository identity and isolated control-state authority. APP/self verification must
not ask that capability to discover, validate, materialize, or export strict executor
authority it does not consume. Release/CI callers explicitly opt into strict executor
authority, whose cgroup/capacity/schema rules remain owned by their runtime owners.
`workflow.py` must not implement those domain rules itself.

### Plan owns certification order

The APP Plan owns Goon ordering. Workflow never enumerates, duplicates, resumes,
skips or reorders Goons.

### Development runner owns only feedback scheduling

`scripts/development_test.sh` may name canonical development Make targets because
its product is explicitly the local feedback schedule, not APP certification. It
must not call the APP Plan, strict certification frontdoor, browser certification,
format/lint or release targets.
