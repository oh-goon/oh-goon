# oci/publish-image

**Production template. Production Composite Goon.**

Build one OCI archive, publish that workspace explicitly, then copy the archive to a digest-qualified registry reference through Fleet's typed publisher. Credentials are named host-backed env sources used only to create an attempt-private auth file. No mutable tag is created.

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

## Explicit recipient forwarding

```yaml
- import: oci/image
  pass: [base_registry_hostname, containerfile, context, platform]
- import: oci/publish
  pass: [publish_registry_hostname, publish_repository]
  with:
    source_workspace: ${{ goons.oci-image.workspace }}
```

The six public scalar inputs use exactly six explicit same-name recipient
bindings. `source_workspace` remains a producer-workspace `with:` reference;
publication outputs and native credentials are unchanged.
