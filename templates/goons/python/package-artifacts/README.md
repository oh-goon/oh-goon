# python/package-artifacts

Run the project’s packaging command and retain its distribution bundle and manifest evidence.

**Status:** runnable with prerequisites. **Effects:** Runs the project packaging command and retains distribution bytes.

**Prerequisites:** The selected locked group must supply `package-artifacts`; strict content-store publication must be configured.

```sh
oh-goon templates init python/package-artifacts --kind plan --output ./workflow
```

The [python/package-artifacts Plan](../../../plans/python/package-artifacts/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `package-distributions.tar` uses content-store retention; `package-artifacts.json` is evidence.

## Before you run

The project must implement a `package-artifacts` CLI accepting `--output-dir`, `--bundle` and `--manifest`, with its build dependencies in the selected lock/group. This is project-specific logic, not a built-in uv command. Use configured strict execution with the Fleet content store for retained bytes.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

The command produces `package-distributions.tar` and `package-artifacts.json` in its project working directory. The bundle uses `content_store`; the manifest uses `evidence`. Native packaging/versioning remains the project command’s responsibility.

See [artifact retention](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md) for Run evidence and retained content.
