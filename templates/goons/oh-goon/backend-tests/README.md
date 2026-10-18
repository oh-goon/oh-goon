# oh-goon/backend-tests

Run the complete bounded canonical backend Python qualification suite.

## Use this Goon

```sh
oh-goon templates show oh-goon/backend-tests --kind goon
oh-goon plan inputs templates/goons/oh-goon/backend-tests/goon.yml
```

Use `goons: [oh-goon/backend-tests]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This Goon owns orchestration/evidence only. The repository-owned `make test-python-complete` command owns test semantics and hard timeout policy; no alternate implementation is introduced.

The strict backend fixtures intentionally use `ldd` as their runtime-dependency inspector and construct inner Plans whose primary command is `true`. Make/script analysis derives both facts from the repository-owned workload. `backend/goon-execution-requirements.json` independently permits the `make:test-python-complete` workload need without restating this Goon's Step semantics, and the generated canonical tool-authority projection transports the exact admitted set into the Step. The Goon YAML does not repeat `ldd`, `true`, or other derived native tools. Fleet still freezes the exact executables into `/toolchain/bin`, so the tests cannot fall through to ambient host tooling.
