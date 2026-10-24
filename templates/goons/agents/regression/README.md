# agents/regression

Run the project's explicitly selected regression executable.

**Status:** runnable with prerequisites. **Effects:** Executes the selected repository script; that script owns its commands and effects.

**Prerequisites:** An executable regression script in the registered repository, with its native tools available.

```sh
oh-goon templates init agents/regression --kind goon --output ./workflow
```

Set `target.repository_id` and `inputs.regression_command: scripts/regression` in `workflow/run.yml`, then [inspect, validate, lock and run](../../../README.md#first-local-python-run). The path is repository-relative; the Goon invokes `./scripts/regression` as one executable. Put arguments or multiple checks in the project script or a project-owned Goon.

**Result:** Native exit status, stdout and stderr determine the Step result. [regression inputs and Step](goon.yml) remain the machine contract. Model or Skill configuration is unnecessary for this script-only phase.
