# oh-goon/integration-contract

Run the existing repository `make test-integration` suite against a **producer-published
source workspace**. The required `source_workspace` input identifies the earlier Step
whose workspace must be explicitly transported to this consumer; it is not an
implicit repository checkout. Missing or invalid producer binding must fail before
Step work.

## Use this Goon

```sh
oh-goon templates show oh-goon/integration-contract --kind goon
oh-goon plan inputs templates/goons/oh-goon/integration-contract/goon.yml
```

Use `goons: [oh-goon/integration-contract]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

The suite itself is **not** a separate generated-API-only test implementation.
`integration-tests` invokes the same native Make target, but uses the admitted
repository workspace by default. These two capabilities are distinct because the
source provenance and resulting evidence differ, not because the command differs.

See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
