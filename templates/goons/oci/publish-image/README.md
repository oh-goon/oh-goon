# oci/publish-image

Build one OCI archive, publish that workspace explicitly, then copy the archive to a digest-qualified registry reference through Fleet's typed publisher. Credentials are named host-backed env sources used only to create an attempt-private auth file. No mutable tag is created.

**Status:** runnable with prerequisites. **Effects:** Builds an image then publishes the exact producer workspace by digest.

**Prerequisites:** A compatible isolated builder, selected base-pull registry and separate destination publication registry with declared host credentials.

```sh
oh-goon templates init oci/publish-image --kind plan --output ./workflow
```

The [oci/publish-image Plan](../../../plans/oci/publish-image/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `OCI_DIGEST` and `OCI_REFERENCE`; the base registry is not inferred as the destination.

## Safety boundary

Containerfile/context inputs use canonical repository-relative syntax; absolute paths, `./...`, `..` segments, and empty path segments are rejected before execution. OCI behavior uses typed builder/registry contracts rather than a Docker socket or arbitrary shell flags. Registry coordinates and credentials are explicit, TLS verification remains enabled, and publish/read operations use deterministic retry policy.

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
