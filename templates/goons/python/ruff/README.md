# python/ruff

Check formatting and lint with Ruff, using the selected project’s native configuration.

**Status:** runnable with prerequisites. **Effects:** Checks formatting and lint without requesting fixes.

**Prerequisites:** The selected locked group must supply Ruff; Ruff reads its native project configuration.

```sh
oh-goon templates init python/ruff --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Native format/lint outcomes.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

Run `ruff format --check --diff`, then `ruff check`. Neither command rewrites source. Native diagnostics and exit codes are recorded in the Run; no GitLab-specific output format is imposed.
