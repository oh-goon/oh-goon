# oci/image

Build one OCI archive through Fleet's typed isolated builder contract. The Goon does not push an image, import registry credentials, expose a Docker socket, or allow arbitrary Buildah flags. External base images must be digest-pinned and come from the explicitly declared registry.

## Use this Goon

```sh
oh-goon templates show oci/image --kind goon
oh-goon plan inputs templates/goons/oci/image/goon.yml
```

Use `goons: [oci/image]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

Prepare the selected OCI execution provider and registry access before execution. Base-image pull and destination publication registries are independent inputs. Credentials use the declared host-backed environment sources, never values files. Inspect digest/workspace outputs through the Run; publishing and retained bytes require their configured owners.

## Contract

- Canonical identity: `oci/image`.
- Repository identity is enforced by the CI/CD execution target; this Goon adds only capability-specific requirements.
- Inputs: `base_registry_hostname`, `containerfile`, `context`, `platform`.
- Steps: `build`.

## Safety boundary

Repository-relative path inputs use canonical syntax: `.` is allowed only as the whole build context; absolute paths, `./...`, `..` segments, and empty path segments are rejected before execution. OCI behavior uses typed builder/registry contracts rather than a Docker socket or arbitrary shell flags. Registry coordinates and credentials are explicit, TLS verification remains enabled, and publish/read operations use deterministic retry policy.

Machine-readable truth is [`goon.yml`](./goon.yml). The README explains intent and safety only; it is never runtime authority.

The archive and metadata paths are Goon-owned implementation details: `oci/image.oci.tar` and `oci/image-build.json`. Callers cannot redirect production evidence paths.

## Registry identity and caller input ownership

See the [shipped OCI input authority audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The base-image pull registry and destination publication registry are not aliases;
only the inputs consumed by this Goon have machine-contract authority here.
