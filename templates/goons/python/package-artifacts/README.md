# python/package-artifacts

Run the project’s packaging command and retain its distribution bundle and manifest evidence.

**Status:** runnable with prerequisites. **Effects:** Runs the project packaging command and retains distribution bytes.

**Prerequisites:** The selected locked group must supply `package-artifacts`; strict content-store publication must be configured.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** `package-distributions.tar` uses content-store retention; `package-artifacts.json` is evidence.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Before you run

The project must implement a `package-artifacts` CLI accepting `--output-dir`, `--bundle` and `--manifest`, with its build dependencies in the selected lock/group. This is project-specific logic, not a built-in uv command. Use configured strict execution with the Fleet content store for retained bytes.

## Configure and compose

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Set the caller choices in `run.yml` as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/package-artifacts` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

The command produces `package-distributions.tar` and `package-artifacts.json` in its project working directory. The bundle uses `content_store`; the manifest uses `evidence`. Native packaging/versioning remains the project command’s responsibility.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).

See [artifact retention](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md) for Run evidence and retained content.
