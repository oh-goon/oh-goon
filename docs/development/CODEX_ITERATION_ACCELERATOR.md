# Codex iteration accelerator

Oh-Goon has one execution system. Self-verification does not get a private runner.

Use:

```text
oh-goon self verify
oh-goon self verify --plan
oh-goon self certify
oh-goon self certify --plan
```

`self verify` selects `templates/plans/oh-goon/self-verification/plan.yml`. `self certify` selects `templates/plans/oh-goon/app-certification/plan.yml`. Both are normal declarative Oh-Goon Plans. Canonical `make init` materializes dependencies once before self-certification; the APP verification Plan consumes that initialized checkout without reacquiring dependencies. Plan resolution, admission, execution ownership, Step evidence, and Game Tape remain on the normal Fleet Plan path rather than a private verifier.

There is deliberately no public `--run-unit` escape hatch. Deterministic test batching remains useful diagnostic/tooling behavior (`oh-goon dev shards ...`), but it cannot become a second scheduler or verifier. Fast verification is made bounded by the canonical Plan's Goons and the normal executor budgets.

`--plan` is projection only: it reports which canonical Plan is selected and the normal `resolve -> lock -> admit -> execute -> evidence` pipeline. It does not synthesize a second plan or execute commands.
