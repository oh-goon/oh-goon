# python/quality

Compose Ruff formatting and lint checks, one type checker and pytest into a reusable Python quality preset.

## Before you run

The selected project has a committed `pyproject.toml` and `uv.lock`; its chosen group provides Ruff, pytest and the selected checker.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/quality --kind goon
oh-goon plan inputs templates/goons/python/quality/goon.yml
```

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Use one caller values file and explicit CLI overrides as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/quality` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Children run in written order and stop on a blocking failure. `project` and `dependency_group` have explicit recipients because all three children use them; `typechecker` projects directly from its single owner, `python/typecheck`. Child Goons own the input contracts. Start with the [local developer journey](../../../README.md#first-local-python-run).

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
