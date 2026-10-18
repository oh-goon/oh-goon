# Shipped Plans

Plans answer only: **which Goon capabilities run, in what order, and which Goon inputs are exposed to the caller?**

Every shipped Plan package is exactly `templates/plans/<category>/<name>/{plan.yml,README.md}`. The package path is the shipped Plan identity, so `name:` is not repeated in YAML and human description belongs in `README.md`.

A minimal Plan is:

```yaml
version: 1
goons:
- python/uv-audit
```

A Plan can expose a directly selected Goon input without copying its schema:

```yaml
version: 1
inputs:
- coverage_threshold
goons:
- python/quality
```

The Goon remains the authority for type, default, choices, ranges and validation. Projection is explicit, not inheritance: an exposed name must belong to exactly one directly selected Goon.

Use `with:` only for a real binding that cannot be represented as same-name projection, for example binding a prior Goon output into a later Goon input:

```yaml
# Real producer-to-consumer binding from templates/plans/oci/release-deploy/plan.yml
- import: kubernetes/release
  with:
    image: ${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}
```

Plans never declare executable Steps, requirements, environment, variables, outputs, cache policy, dependency lists or copied Goon input schemas. One-Goon Plans are valid when the Plan is a useful execution/admission boundary. Plans may not import Plans.

## Resolution and transport

For the difference between input projection, explicit `with:` producer references,
external `from:` values, admission and frozen execution inputs, see the
[canonical template input resolution guide](../../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md).
The Plan only selects and binds ordered Goons; it does not re-resolve inputs at Step
execution or copy the owning Goon's input schema into this README.

## OCI caller-choice review

The [OCI input and registry identity audit](../../docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md)
explains the different base-pull and destination-publish registries and why the
shipped release/deploy Plan retains explicit caller choices instead of an arbitrary
input-count cap. The real [release-deploy Plan](oci/release-deploy/plan.yml) owns the
ordered roster and its exact projection; examples must not substitute a fictional
`registry_hostname` for its two separate registry inputs.
