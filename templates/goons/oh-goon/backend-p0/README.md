# oh-goon/backend-p0

Run the canonical release-critical execution/security P0 closeout suite, including generated-contract verification, without substituting a second certification implementation.

## Use this Goon

```sh
oh-goon templates show oh-goon/backend-p0 --kind goon
oh-goon plan inputs templates/goons/oh-goon/backend-p0/goon.yml
```

Use `goons: [oh-goon/backend-p0]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This is an Oh-Goon repository verification capability. `make verify-p0` owns the selected test/generation semantics; this Goon owns the exact host-tool authority and orchestration/evidence boundary used when that command runs as an APP verification Goon.

The selected P0 tests construct strict nested Fleet executions. Their shared test fixture consumes this Goon's admitted `git`, `ldd`, `sh`, and `uv` authority; it does not inherit the broader diagnostic `oh-goon/backend-tests` tool set. Plan-specific test harnesses derive their own exact toolchain from the resolved inner Plan instead of reusing the P0 fixture authority.

This keeps the release-critical boundary least-privilege: `node`/`npm` remain absent from P0 unless a P0 test really requires them, while non-P0 tests that resolve Plans containing those tools receive plan-scoped test authority.

Shell authority (`sh`): Nested P0 Fleet fixtures require admitted sh tool authority, independent of the direct make argv.
