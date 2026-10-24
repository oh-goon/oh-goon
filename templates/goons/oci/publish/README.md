# oci/publish

Publishes one OCI image archive from a previously published Goon workspace to the configured registry.

**Status:** runnable with prerequisites. **Effects:** Publishes the exact earlier producer workspace by immutable digest.

**Prerequisites:** A valid typed `source_workspace` from an earlier producer; destination registry access with declared `OCI_REGISTRY_USERNAME`/`OCI_REGISTRY_PASSWORD` host bindings.

**Use:** compose this Goon after an image producer and bind its published workspace; start with the [build-and-publish Plan](../../../plans/oci/publish-image/README.md). [`goon.yml`](goon.yml) owns the typed inputs and outputs.

**Expected outputs:** `OCI_DIGEST` and `OCI_REFERENCE`.

## Registry identity and caller input ownership

See the [shipped OCI input authority audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The base-image pull registry and destination publication registry are not aliases;
only the inputs consumed by this Goon have machine-contract authority here.
