# python/uv-audit

Audit the repository-root uv lock with native uv and record its vulnerability result.

**Status:** runnable with prerequisites. **Effects:** Queries vulnerability data for the repository-root uv lock.

**Prerequisites:** Native uv with audit support, a repository-root uv lock and declared `host_unrestricted` egress. No dependency-group installation is declared.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Native JSON audit output and actual Step status.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Before you run

The repository root contains the committed uv project and lock, and native uv supports its preview audit command. The scanner uses the explicitly declared online egress grant; configure any required native transport before running.

## Configure and compose

Add `python/uv-audit` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Native uv owns dependency and vulnerability interpretation. Its JSON stdout and exit status are Run evidence. This Goon does not create a separate audit report file or rewrite the lock.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).

Scanner database updates use unrestricted network access. See [network settings](../../../../docs/development/TEMPLATE_NETWORK_AUTHORITY.md) for the available isolation policies.
