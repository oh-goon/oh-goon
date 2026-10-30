# Shipped Plans

Plans answer only: **which Goon capabilities run, in what order, and which Goon inputs are exposed to the caller?**

Every shipped Plan package is exactly `templates/plans/<category>/<name>/{plan.yml,README.md}`. The package path is the shipped Plan identity, so `name:` is not repeated in YAML and human description belongs in `README.md`.

Start a catalog Plan with `oh-goon init --template python/quality --kind plan --output ./workflow --draft`; its `run.yml` selects the catalog source directly.

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../README.md).

A minimal authored Plan is:

```yaml
goons:
- python/uv-audit
```

A plain import automatically exposes its unambiguous public inputs. To fix an intentional override in the Plan:

```yaml
goons:
- import: python/quality
  with:
    typechecker: ty
```

The Goon remains the authority for type, default, choices, ranges and validation. No Plan `inputs:` or `pass:` declaration is needed for a plain unique import. Fixed bindings are excluded from automatic caller inputs. Shared names keep independent defaults and require explicit `pass:`/`with:` routing before caller overrides. Optional `inputs: [name, ...]` restricts the Plan's public interface without copying schemas.

Use `with:` for fixed values, renamed caller inputs or dataflow, for example binding a prior Goon output into a later Goon input:

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

## Extend a Plan

Follow the [developer journey](../README.md#append-an-existing-capability). Plans and Composite Goons both support explicit import-local `pass:` and `with:`. A unique named input projects its child contract automatically; multiple recipients require explicit bindings. Project-owned packages use an explicit `from:` directory and never shadow shipped imports.

For task-specific customization, see [independent values for repeated imports](../README.md#give-repeated-imports-independent-values) and [changing the order of work](../README.md#change-the-order-of-work). Select Atomic Goons directly when you need another order inside a Composite. Producer outputs and workspaces must come from earlier imports.
