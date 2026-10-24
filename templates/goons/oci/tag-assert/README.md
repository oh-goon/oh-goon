# oci/tag-assert

Read one existing OCI registry tag and require that its top-level manifest digest exactly matches the caller-declared sha256 digest. This Goon never creates, moves, or deletes a tag. Registry credentials are named host-backed sources used only through an attempt-private auth file.

**Status:** runnable with prerequisites. **Effects:** Reads/asserts the digest of one existing registry tag.

**Prerequisites:** Selected registry/repository/tag and expected digest, with declared `OCI_REGISTRY_USERNAME`/`OCI_REGISTRY_PASSWORD` host bindings.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** `OCI_TAG_DIGEST` and `OCI_TAG_RESOLVED_REFERENCE`; mismatch fails without tag mutation.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

## Contract

- Canonical identity: `oci/tag-assert`.
- Repository identity is enforced by the CI/CD execution target; this Goon adds only capability-specific requirements.
- Inputs: `registry_hostname`, `repository`, `tag`, `expected_digest`.
- Steps: `assert`.
- Public outputs: `OCI_TAG_DIGEST`, `OCI_TAG_RESOLVED_REFERENCE`.

## Safety boundary

The expected digest is validated as exact lowercase `sha256:` plus 64 hexadecimal characters before registry access. OCI behavior uses typed builder/registry contracts rather than a Docker socket or arbitrary shell flags. Registry coordinates and credentials are explicit, TLS verification remains enabled, and publish/read operations use deterministic retry policy.

Machine-readable truth is [`goon.yml`](./goon.yml). The README explains intent and safety only; it is never runtime authority.
