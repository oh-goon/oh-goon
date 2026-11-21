# agents/regression

Run the project's explicitly selected regression executable.

**Status:** runnable with prerequisites. **Effects:** Executes the selected repository script; that script owns its commands and effects.

**Prerequisites:** An executable regression script in the registered repository, with its native tools available.

```sh
oh-goon init --template agents/regression --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and `inputs.regression_command: scripts/regression` in `workflow/run.yml`, then [inspect, validate, lock and run](../../../README.md#first-local-python-run). The path is repository-relative; the Goon invokes `./scripts/regression` as one executable. Put arguments or multiple checks in the project script or a project-owned Goon.

**Result:** Native exit status, stdout and stderr determine the Step result. [regression inputs and Step](goon.yml) remain the machine contract. Model or Skill configuration is unnecessary for this script-only phase.

The script contract uses exit `1` for ordinary failed code checks, exit `0` for
success, and distinct nonreserved codes for environment or unsupported failures.
On an ordinary failure, write a concise non-secret diagnostic to stdout. Up to
2048 complete UTF-8 bytes are explicitly retained through the original Step output
owner, with native stream provenance and secret redaction. Missing, truncated or
invalid feedback does not authorize another repair.
The diagnostic output is required only after failure; successful checks do not
publish it and may produce more stdout than this feedback bound.

In a bounded review/repair campaign, a safely settled declared failure can supply
context for fresh review and repair Runs. The failed Step and Run remain failed
history; retained feedback never accepts exit `1` as success. A failed check still
stops an ordinary Plan. Only the bounded campaign can admit another fresh phase
Run after validating the failed result and its remaining authority.
Other exit protocols require an explicitly authored project-owned Goon with its
own regression failure declaration. No classification is inferred from log prose.
