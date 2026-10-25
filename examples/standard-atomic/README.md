# Standard Atomic workflow

Classification: runnable-with-prerequisites

Run the checked-in shell Goon through one selected `run.yml`, fresh locking, Fleet admission and durable Game Tape. The script prints `oh-goon-atomic-ok`. Its maintenance policy uses ordinary trusted local execution for operator-owned code.

Use an initialized Oh-Goon installation with native Git and `sh`. Copy this directory into a separate workload location. Initialize that copy as a Git repository if necessary, register its parent directory, and read its exact target ID:

```sh
git init /absolute/path/to/atomic
oh-goon league add-folder /absolute/path/to
oh-goon league scan --json
```

Set `target.repository_id` in the included [`run.yml`](run.yml) to the copied repository's ID. The `repository-goon` source selects its exact [`goon.yml`](goon.yml). From the copied directory:

```sh
oh-goon config inspect ./run.yml
oh-goon config validate ./run.yml
oh-goon lock ./run.yml
oh-goon run ./run.yml --json
oh-goon plan tape RUN_ID --json
```

Lock explicitly refreshes the workflow identity for your selection. Run waits for native execution; successful completion returns a Run ID and `SUCCESS`. Game Tape records the Step/process result and output evidence. There is no Python workload or workload dependency-group setup.

The native integration checks copy these actual files and cover subprocess success, nonzero exit, cancellation, timeout and changed Goon definitions:

```sh
uv run --project backend --offline --frozen --no-sync pytest -q backend/tests/integration/test_exec01_standard_atomic_linux.py
```

The separate [canonical example regression](../../backend/tests/integration/services/test_example_workflows.py) executes the installed CLI through lock, Fleet completion and durable Game Tape readback. The direct Step tests retain their narrower process boundary; neither path establishes privileged containment or APP certification.
