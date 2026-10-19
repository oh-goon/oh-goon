# oh-goon/tooling-tests

Run the complete canonical repository tooling test suite.

## Use this Goon

```sh
oh-goon templates show oh-goon/tooling-tests --kind goon
oh-goon plan inputs templates/goons/oh-goon/tooling-tests/goon.yml
```

Use `goons: [oh-goon/tooling-tests]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This Goon owns orchestration/evidence only. The existing repository command owns test semantics; no parallel test implementation or alternate lifecycle is introduced.

The complete tooling suite also owns `bash` explicitly because `scripts/tests/test_run_logging.py` launches nested POSIX shell processes dynamically through Python `subprocess`; this child edge is not derivable from Make recipe syntax and is therefore declared as an opaque workload tool.

Shell authority (`bash`): Nested subprocess tests in test_run_logging.py require admitted bash tool authority, independent of the direct make argv.
