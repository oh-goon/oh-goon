# python/test

Run pytest with native project discovery and policy, and produce a JUnit report.

**Status:** runnable with prerequisites. **Effects:** Executes the project’s pytest workload and writes a test report.

**Prerequisites:** The selected locked group must supply pytest; native pytest configuration owns discovery and coverage policy.

```sh
oh-goon templates init python/test --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `junit.xml` in the selected project, retained as evidence.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

`junit.xml` is written in the selected project and recorded as artifact evidence. A nonzero pytest exit remains a failed Step. The template does not force a `src` layout, coverage target or coverage floor.

See [artifact retention](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md) for Run evidence and retained content.
