# python/profile

Profile the project’s pytest workload with Pyinstrument and retain evidence of the HTML report.

**Status:** runnable with prerequisites. **Effects:** Executes and profiles the real pytest workload.

**Prerequisites:** The selected locked group must supply pytest and Pyinstrument.

```sh
oh-goon init --template python/profile --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `pytest-profile.html`, retained as evidence.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

`pytest-profile.html` is written in the selected project. Local runs leave it there for viewing; evidence records its hash and size, rather than retaining its bytes after an isolated workspace is disposed. Pytest policy remains active while profiling.

See [artifact retention](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md) for Run evidence and retained content.
