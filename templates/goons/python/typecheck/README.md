# python/typecheck

Run one explicitly selected type checker against its native project configuration.

**Status:** runnable with prerequisites. **Effects:** Runs the caller-selected native type checker.

**Prerequisites:** The selected locked group must supply the selected Pyright or ty command; its native configuration owns analysis policy.

```sh
oh-goon templates init python/typecheck --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** The selected checker’s actual exit result.

## Inputs

`project` selects the repository-relative uv project directory; `dependency_group` selects its locked tools. Commands run from that project by default, so native configuration, tests and report paths follow the same selection. Defaults are `.` and `dev`.

## Result

Only the selected checker runs. To run both, import this Goon twice with distinct `as:` names and explicit `with: {typechecker: ...}` values. Check scope and rules belong in native configuration.
