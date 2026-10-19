# oci/publish-image

Build one OCI archive, publish that workspace explicitly, then copy the archive to a digest-qualified registry reference through Fleet's typed publisher. Credentials are named host-backed env sources used only to create an attempt-private auth file. No mutable tag is created.

## Use this Goon

```sh
oh-goon templates show oci/publish-image --kind goon
oh-goon plan inputs templates/goons/oci/publish-image/goon.yml
```

Use `goons: [oci/publish-image]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

Prepare the selected OCI execution provider and registry access before execution. Base-image pull and destination publication registries are independent inputs. Credentials use the declared host-backed environment sources, never values files. Inspect digest/workspace outputs through the Run; publishing and retained bytes require their configured owners.

## Contract

- Canonical identity: `oci/publish-image`.
- Repository identity is enforced by the CI/CD execution target; this Goon adds only capability-specific requirements.
- Inputs: `base_registry_hostname`, `publish_registry_hostname`, `publish_repository`, `containerfile`, `context`, `platform`.
- Steps: `build`, `publish`.
- Public outputs: `OCI_DIGEST`, `OCI_REFERENCE`.

## Safety boundary

Containerfile/context inputs use canonical repository-relative syntax; absolute paths, `./...`, `..` segments, and empty path segments are rejected before execution. OCI behavior uses typed builder/registry contracts rather than a Docker socket or arbitrary shell flags. Registry coordinates and credentials are explicit, TLS verification remains enabled, and publish/read operations use deterministic retry policy.

Machine-readable truth is [`goon.yml`](./goon.yml). The README explains intent and safety only; it is never runtime authority.

## Registry identity and caller input ownership

See the [shipped OCI input authority audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The base-image pull registry and destination publication registry are not aliases;
only the inputs consumed by this Goon have machine-contract authority here.

## Compose and bind dataflow

```yaml
- oci/image
- import: oci/publish
  with:
    source_workspace: ${{ goons.oci-image.workspace }}
```

Each public scalar input has one direct owner, so the Composite's `inputs:` list
needs no repeated forwarding. `source_workspace` is an explicit producer-workspace
`with:` reference. Public outputs and native credentials keep their own declarations.
