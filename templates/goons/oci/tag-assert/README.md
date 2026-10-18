# oci/tag-assert

Read one existing OCI registry tag and require that its top-level manifest digest exactly matches the caller-declared sha256 digest. This Goon never creates, moves, or deletes a tag. Registry credentials are named host-backed sources used only through an attempt-private auth file.

## Use this Goon

```sh
oh-goon templates show oci/tag-assert --kind goon
oh-goon plan inputs templates/goons/oci/tag-assert/goon.yml
```

Use `goons: [oci/tag-assert]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

Prepare the selected OCI execution provider and registry access before execution. Base-image pull and destination publication registries are independent inputs. Credentials use the declared host-backed environment sources, never values files. Inspect digest/workspace outputs through the Run; publishing and retained bytes require their configured owners.

## Contract

- Canonical identity: `oci/tag-assert`.
- Repository identity is enforced by the CI/CD execution target; this Goon adds only capability-specific requirements.
- Inputs: `registry_hostname`, `repository`, `tag`, `expected_digest`.
- Steps: `assert`.
- Public outputs: `OCI_TAG_DIGEST`, `OCI_TAG_RESOLVED_REFERENCE`.

## Safety boundary

The expected digest is validated as exact lowercase `sha256:` plus 64 hexadecimal characters before registry access. OCI behavior uses typed builder/registry contracts rather than a Docker socket or arbitrary shell flags. Registry coordinates and credentials are explicit, TLS verification remains enabled, and publish/read operations use deterministic retry policy.

Machine-readable truth is [`goon.yml`](./goon.yml). The README explains intent and safety only; it is never runtime authority.
