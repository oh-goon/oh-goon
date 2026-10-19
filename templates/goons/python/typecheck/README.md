# python/typecheck

Run one explicitly selected type checker against its native project configuration.

## Before you run

The selected locked group provides the chosen checker. Set `typechecker` to `pyright` or `ty`; the default is `pyright`. Prepare Pyright's native Node runtime when selecting Pyright. There is no tool autodetection or fallback.

## Configure and compose

Inspect the current contract and copy the source when authoring your own capability:

```sh
oh-goon templates show python/typecheck --kind goon
oh-goon plan inputs templates/goons/python/typecheck/goon.yml
```

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`. Use one caller values file and explicit CLI overrides as shown in the [template guide](../../../README.md#change-values-without-editing-the-goon).

Add `python/typecheck` to a Plan’s ordered `goons:` list with a plain import to keep its defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed choices or dataflow; use `pass:` when shared names need explicit recipients. Refresh the lock with the same values before running.

## Result and extension

Only the selected checker runs. To run both, import this Goon twice with distinct `as:` names and explicit `with: {typechecker: ...}` values. Check scope and rules belong in native configuration.

Append another Goon to the Plan to add a capability. To change this command sequence, create a project-owned Atomic Goon and import it with an explicit `from:` source. See [authoring and local imports](../../../README.md#append-your-own-capability).
