# python/dependency-inventory

Observe qualified `uv.lock` inventory through Hashmarks. This read-only Step does not run uv, parse declarations or inspect installed packages.

**Status:** runnable with prerequisites. **Effects:** Reads supported dependency manifests and records inventory evidence.

**Prerequisites:** A registered repository containing manifests. This `uses: dependency_inventory` observer neither installs dependencies nor selects a uv dependency group.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Dependency inventory evidence; later maintenance remains a separate explicit action.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Before you run

Select the repository whose manifests you want to inspect. This observation is repository-wide rather than a Python environment installation.

## Configure and compose

Add `python/dependency-inventory` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Inspect the recorded dependency evidence in the Run. Inventory is an observation; it does not install dependencies or make a vulnerability or test-success claim.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
