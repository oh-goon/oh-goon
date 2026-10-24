# oh-goon/app-certification

Canonical final APP verification for one exact initialized repository revision.

**Status:** internal owner command. Use this repository’s owner command rather than initializing a portable user workflow.

**Effects and prerequisites:** uses the prepared Oh-Goon checkout and its declared native commands. Run `make init` once to materialize the committed dependencies; verification consumes that environment.

```sh
make certify
```

Checks execution/security, architecture, catalog startability, frontend functionality and local runtime start/probe/stop. Catalog startability stops before workload Steps. Broader diagnostics, native qualification and artifact certification remain separate owners.

**Outputs and failures:** native checks, Run/Step evidence and Game Tape preserve their actual results. A failed, unavailable or interrupted check stays unverified. No model statement or diagnostic result substitutes for canonical certification.

**Customize:** change the owning repository contract and focused regressions; do not copy this internal Plan into a different product. See the [execution/certification contract](../../../../docs/CI_CD_CONTRACT.md).
