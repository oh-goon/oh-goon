# Standard Linux/WSL Atomic Goon

Classification: integration-fixture

Exercise actual Linux subprocesses through the production resolver and direct Step executor. This shell-only Goon selects the maintenance purpose for operator-owned trusted code. It is not an untrusted-code isolation sandbox.

The fixture includes its source, script and lock. The focused integration uses a disposable operator-owned copy and checks real success, nonzero exit, cancellation, command timeout and Goon-definition drift:

```sh
uv run --project backend --offline --frozen --no-sync pytest -q backend/tests/integration/test_exec01_standard_atomic_linux.py
```

Run from the Oh-Goon source root after native setup. There is no Python workload here, so no workload `uv.lock` is applicable. Privileged custody and native containment are separate qualification boundaries.

This is a physical Step integration slice. It does not establish complete Fleet admission, a terminal Run and durable Game Tape end to end. For normal runnable workflows use [one selected `run.yml`](../../templates/README.md), canonical lock and fresh Run admission; this direct-executor fixture is not an alternate public launch path.
