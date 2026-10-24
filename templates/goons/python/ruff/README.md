# python/ruff

Check formatting and lint with Ruff, using the selected project’s native configuration.

**Status:** runnable with prerequisites. **Effects:** Checks formatting and lint without requesting fixes.

**Prerequisites:** The selected locked group must supply Ruff; Ruff reads its native project configuration.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Native format/lint outcomes.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Before you run

The selected locked dependency group provides `ruff`. Configure scope, exclusions and rules in the project’s Ruff configuration.

## Configure and compose

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Set the caller choices in `run.yml` as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/ruff` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Run `ruff format --check --diff`, then `ruff check`. Neither command rewrites source. Native diagnostics and exit codes are recorded in the Run; no GitLab-specific output format is imposed.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
