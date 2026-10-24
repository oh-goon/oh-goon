# python/quality

Compose Ruff formatting and lint checks, one type checker and pytest into a reusable Python quality preset.

**Status:** runnable with prerequisites. **Effects:** Runs the declared native project command and records its actual result.

**Prerequisites:** A committed Python project and uv lock with the selected native commands available in its locked group. Native project configuration owns tool settings.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Native command status plus the outputs/artifacts declared in `goon.yml`.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Before you run

The selected project has a committed `pyproject.toml` and `uv.lock`; its chosen group provides Ruff, pytest and the selected checker.

## Configure and compose

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Set the caller choices in `run.yml` as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/quality` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Children run in written order and stop on a blocking failure. `project` and `dependency_group` have explicit recipients because all three children use them; `typechecker` projects directly from its single owner, `python/typecheck`. Child Goons own the input contracts. Start with the [local developer journey](../../../README.md#first-local-python-run).

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
