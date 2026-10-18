# python/profile

Profile the project’s pytest workload with Pyinstrument and retain evidence of the HTML report.

## Before you run

The selected locked group provides `pyinstrument`, `pytest` and any plugins required by project configuration.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/profile --kind goon
oh-goon plan inputs templates/goons/python/profile/goon.yml
```

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Use one caller values file and explicit CLI overrides as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/profile` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

`pytest-profile.html` is written in the selected project. Local runs leave it there for viewing; evidence records its hash and size, rather than retaining its bytes after an isolated workspace is disposed. Pytest policy remains active while profiling.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
