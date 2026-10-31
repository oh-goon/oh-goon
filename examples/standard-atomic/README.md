# Standard Atomic workflow

Classification: runnable-with-prerequisites

Run the checked-in shell Goon through one selected `run.yml`, fresh locking, Fleet admission and durable Game Tape. The script prints `oh-goon-atomic-ok`. Its maintenance policy uses ordinary trusted local execution for operator-owned code.

Use an initialized Oh-Goon installation with native Git and `sh`. Copy this
directory into a separate workload location and set `target.repository: .` in
its included [`run.yml`](run.yml). Its `repository-goon` source selects the exact
[`goon.yml`](goon.yml). From the copied directory:

```sh
git init .
oh-goon workflow prepare ./run.yml --register-target --no-input
oh-goon run ./run.yml --no-input --json
oh-goon runs tape RUN_ID --json
```

Preparation registers only this exact Git root through the existing registration
owner when needed. The authored target remains a path; no repository-ID copying
or parent scan is needed.

Lock explicitly refreshes the workflow identity for your selection. Run waits for native execution; successful completion returns a Run ID and `SUCCESS`. Game Tape records the Step/process result and output evidence. There is no Python workload or workload dependency-group setup.

The native integration checks copy these actual files and cover subprocess success, nonzero exit, cancellation, timeout and changed Goon definitions:

```sh
uv run --project backend --offline --frozen --no-sync pytest -q backend/tests/integration/test_exec01_standard_atomic_linux.py
```

The separate [canonical example regression](../../backend/tests/integration/services/test_example_workflows.py) executes the installed CLI through lock, Fleet completion and durable Game Tape readback. The direct Step tests retain their narrower process boundary; neither path establishes privileged containment or APP certification.
