# python/package-artifacts

Run the project’s packaging command and retain its distribution bundle and manifest evidence.

## Before you run

The project must implement a `package-artifacts` CLI accepting `--output-dir`, `--bundle` and `--manifest`, with its build dependencies in the selected lock/group. This is project-specific logic, not a built-in uv command. Use configured strict execution with the Fleet content store for retained bytes.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/package-artifacts --kind goon
oh-goon plan inputs templates/goons/python/package-artifacts/goon.yml
```

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Use one caller values file and explicit CLI overrides as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/package-artifacts` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

The command produces `package-distributions.tar` and `package-artifacts.json` in its project working directory. The bundle uses `content_store`; the manifest uses `evidence`. Native packaging/versioning remains the project command’s responsibility.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
