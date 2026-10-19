# oh-goon/integration-tests

Run the native `make test-integration` suite against the **admitted repository**.
There is no `source_workspace` producer requirement for this standalone internal
verification capability. The repository's Make target owns the integration test
selection and pytest execution; this Goon owns ordering and evidence only.

## Use this Goon

```sh
oh-goon templates show oh-goon/integration-tests --kind goon
oh-goon plan inputs templates/goons/oh-goon/integration-tests/goon.yml
```

Use `goons: [oh-goon/integration-tests]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

`integration-contract` delegates the same command but consumes an **explicitly
published earlier-Step workspace**. The different source authority makes the
results semantically different even though argv is identical; do not merge them.

See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
