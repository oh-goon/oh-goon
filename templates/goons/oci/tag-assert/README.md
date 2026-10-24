# oci/tag-assert

Read one existing OCI registry tag and require that its top-level manifest digest exactly matches the caller-declared sha256 digest. This Goon never creates, moves, or deletes a tag. Registry credentials are named host-backed sources used only through an attempt-private auth file.

**Status:** runnable with prerequisites. **Effects:** Reads/asserts the digest of one existing registry tag.

**Prerequisites:** Selected registry/repository/tag and expected digest, with declared `OCI_REGISTRY_USERNAME`/`OCI_REGISTRY_PASSWORD` host bindings.

```sh
oh-goon templates init oci/tag-gate --kind plan --output ./workflow
```

The [oci/tag-gate Plan](../../../plans/oci/tag-gate/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** `OCI_TAG_DIGEST` and `OCI_TAG_RESOLVED_REFERENCE`; mismatch fails without tag mutation.

## Safety boundary

The expected digest is validated as exact lowercase `sha256:` plus 64 hexadecimal characters before registry access. OCI behavior uses typed builder/registry contracts rather than a Docker socket or arbitrary shell flags. Registry coordinates and credentials are explicit, TLS verification remains enabled, and publish/read operations use deterministic retry policy.
