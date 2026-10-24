# python/release-metadata

Run the project’s release-metadata command to publish explicit project and release versions.

**Status:** runnable with prerequisites. **Effects:** Runs the project metadata command and writes its output files.

**Prerequisites:** The selected locked group must supply `release-metadata`; select the intended revision/channel.

```sh
oh-goon templates init python/release-metadata --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `project_version` and `release_version`; calculation does not stamp a new source version.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

The command writes `.fleet/release/PROJECT_VERSION` and `.fleet/release/RELEASE_VERSION` in its project. Public outputs expose those scalar values to later Goons. This capability does not edit the project version or publish a release.
