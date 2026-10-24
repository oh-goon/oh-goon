# python/release-metadata

Run the project’s release-metadata command to publish explicit project and release versions.

**Status:** runnable with prerequisites. **Effects:** Runs the project metadata command and writes its output files.

**Prerequisites:** The selected locked group must supply `release-metadata`; select the intended revision/channel.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** `project_version` and `release_version`; calculation does not stamp a new source version.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Before you run

The project must implement a `release-metadata` CLI accepting `--channel`, `--revision` and `--output-dir`. Its selected lock/group supplies that command. Select the channel and exact revision explicitly.

## Configure and compose

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Set the caller choices in `run.yml` as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/release-metadata` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

The command writes `.fleet/release/PROJECT_VERSION` and `.fleet/release/RELEASE_VERSION` in its project. Public outputs expose those scalar values to later Goons. This capability does not edit the project version or publish a release.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
