# python/quality

Compose Ruff formatting and lint checks, one type checker and pytest into a reusable Python quality preset.

**Status:** runnable with prerequisites. **Effects:** Runs the declared native project command and records its actual result.

**Prerequisites:** A committed Python project and uv lock with the selected native commands available in its locked group. Native project configuration owns tool settings.

```sh
oh-goon init --template python/quality --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Native command status plus the outputs/artifacts declared in `goon.yml`.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

Children run in written order and stop on a blocking failure. `project` and `dependency_group` have explicit recipients because all three children use them; `typechecker` projects directly from its single owner, `python/typecheck`. Child Goons own the input contracts. Start with the [local developer journey](../../../README.md#first-local-python-run).
