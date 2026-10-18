# python/release-metadata

Run the project’s release-metadata command to publish explicit project and release versions.

## Before you run

The project must implement a `release-metadata` CLI accepting `--channel`, `--revision` and `--output-dir`. Its selected lock/group supplies that command. Select the channel and exact revision explicitly.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/release-metadata --kind goon
oh-goon plan inputs templates/goons/python/release-metadata/goon.yml
```

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Use one caller values file and explicit CLI overrides as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/release-metadata` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

The command writes `.fleet/release/PROJECT_VERSION` and `.fleet/release/RELEASE_VERSION` in its project. Public outputs expose those scalar values to later Goons. This capability does not edit the project version or publish a release.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
