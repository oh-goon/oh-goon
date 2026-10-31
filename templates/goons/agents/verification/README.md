# agents/verification

Run the project's explicitly selected verification executable.

**Status:** runnable with prerequisites. **Effects:** Executes the selected repository script; that script owns its commands and effects.

**Prerequisites:** An executable verification script in the registered repository, with its native tools available.

```sh
oh-goon init --template agents/verification --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and `inputs.verification_command: scripts/verification` in `workflow/run.yml`, then [inspect, validate, lock and run](../../../README.md#first-local-python-run). The path is repository-relative; the Goon invokes `./scripts/verification` as one executable. Put arguments or multiple checks in the project script or a project-owned Goon.

**Result:** Native exit status, stdout and stderr determine the Step result. [verification inputs and Step](goon.yml) remain the machine contract. Model or Skill configuration is unnecessary for this script-only phase.
