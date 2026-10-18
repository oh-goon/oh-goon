# python/test

Run pytest with native project discovery and policy, and produce a JUnit report.

## Before you run

The selected locked group provides `pytest`, plus the plugins required by your own pytest configuration. Coverage is optional; install pytest-cov and configure its source and threshold in the project when needed.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/test --kind goon
oh-goon plan inputs templates/goons/python/test/goon.yml
```

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Use one caller values file and explicit CLI overrides as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/test` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

`junit.xml` is written in the selected project and recorded as artifact evidence. A nonzero pytest exit remains a failed Step. The template does not force a `src` layout, coverage target or coverage floor.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).

See [artifact retention](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md) for Run evidence and retained content.
