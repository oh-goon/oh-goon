# oci/image

Build one OCI archive through Fleet's typed isolated builder contract. The Goon does not push an image, import registry credentials, expose a Docker socket, or allow arbitrary Buildah flags. External base images must be digest-pinned and come from the explicitly declared registry.

**Status:** runnable with prerequisites. **Effects:** Builds and retains one image workspace; pulls bases from the selected registry.

**Prerequisites:** An admitted strict executor with Buildah and crun, digest-pinned Containerfile bases and configured retained workspace storage. No publish destination or publication credential binding is selected. The current local backend refuses strict Runs until independent native parent custody can be established; installing these tools alone does not qualify it. See the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init oci/image --kind plan --output ./workflow
```

The [oci/image Plan](../../../plans/oci/image/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `oci/image.oci.tar`, `oci/image-build.json` and a published producer workspace.

## Safety boundary

Repository-relative path inputs use canonical syntax: `.` is allowed only as the whole build context; absolute paths, `./...`, `..` segments, and empty path segments are rejected before execution. OCI behavior uses typed builder/registry contracts rather than a Docker socket or arbitrary shell flags. Registry coordinates and credentials are explicit, TLS verification remains enabled, and publish/read operations use deterministic retry policy.

The archive and metadata paths are Goon-owned implementation details: `oci/image.oci.tar` and `oci/image-build.json`. Callers cannot redirect production evidence paths.

Buildah uses OCI isolation and the frozen crun companion for Containerfile `RUN` instructions. Their inner network is disabled with `--network none`; base-image pulls use the separately admitted registry boundary.

## Registry identity and caller input ownership

See the [shipped OCI input authority audit](../../../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md).
The base-image pull registry and destination publication registry are not aliases;
only the inputs consumed by this Goon have machine-contract authority here.
