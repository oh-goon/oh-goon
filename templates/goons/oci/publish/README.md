# oci/publish

Publishes one OCI image archive from a previously published Goon workspace to the configured registry.

**Status:** runnable with prerequisites. **Effects:** Publishes the exact earlier producer workspace by immutable digest.

**Prerequisites:** A valid typed `source_workspace` from an earlier producer; destination registry access with declared `OCI_REGISTRY_USERNAME`/`OCI_REGISTRY_PASSWORD` host bindings.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** `OCI_DIGEST` and `OCI_REFERENCE`.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Registry identity and caller input ownership

See the [shipped OCI input authority audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The base-image pull registry and destination publication registry are not aliases;
only the inputs consumed by this Goon have machine-contract authority here.
