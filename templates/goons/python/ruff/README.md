# python/ruff

Check formatting and lint with Ruff, using the selected project’s native configuration.

## Before you run

The selected locked dependency group provides `ruff`. Configure scope, exclusions and rules in the project’s Ruff configuration.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/ruff --kind goon
oh-goon plan inputs templates/goons/python/ruff/goon.yml
```

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Use one caller values file and explicit CLI overrides as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/ruff` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Run `ruff format --check --diff`, then `ruff check`. Neither command rewrites source. Native diagnostics and exit codes are recorded in the Run; no GitLab-specific output format is imposed.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
