# python/profile

Profile the project’s pytest workload with Pyinstrument and retain evidence of the HTML report.

**Status:** runnable with prerequisites. **Effects:** Executes and profiles the real pytest workload.

**Prerequisites:** The selected locked group must supply pytest and Pyinstrument.

```sh
oh-goon templates init python/profile --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `pytest-profile.html`, retained as evidence.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

`pytest-profile.html` is written in the selected project. Local runs leave it there for viewing; evidence records its hash and size, rather than retaining its bytes after an isolated workspace is disposed. Pytest policy remains active while profiling.

See [artifact retention](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md) for Run evidence and retained content.
