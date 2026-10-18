# oh-goon/integration-tests

Run the native `make test-integration` suite against the **admitted repository**.
There is no `source_workspace` producer requirement for this standalone internal
verification capability. The repository's Make target owns the integration test
selection and pytest execution; this Goon owns ordering and evidence only.

`integration-contract` delegates the same command but consumes an **explicitly
published earlier-Step workspace**. The different source authority makes the
results semantically different even though argv is identical; do not merge them.

See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
