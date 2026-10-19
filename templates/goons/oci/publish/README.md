# oci/publish

Publishes one OCI image archive from a previously published Goon workspace to the configured registry.

## Use this Goon

```sh
oh-goon templates show oci/publish --kind goon
oh-goon plan inputs templates/goons/oci/publish/goon.yml
```

Use `goons: [oci/publish]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

Prepare the selected OCI execution provider and registry access before execution. Base-image pull and destination publication registries are independent inputs. Credentials use the declared host-backed environment sources, never values files. Inspect digest/workspace outputs through the Run; publishing and retained bytes require their configured owners.

## Registry identity and caller input ownership

See the [shipped OCI input authority audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The base-image pull registry and destination publication registry are not aliases;
only the inputs consumed by this Goon have machine-contract authority here.
