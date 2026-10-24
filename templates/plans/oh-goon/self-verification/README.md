# oh-goon/self-verification

Run the fast internal verification graph for one initialized Oh-Goon revision.

**Status:** internal owner command. Use this repository’s owner command rather than initializing a portable user workflow.

**Effects and prerequisites:** uses the prepared Oh-Goon checkout and its declared native commands. Run `make init` once to materialize the committed dependencies; verification consumes that environment.

```sh
oh-goon self verify
```

**What to edit / settings owner:** repository owners maintain [`plan.yml`](plan.yml), its imported Goons and the Make lifecycle. This package is not a general user configuration example.

The Plan selects backend P0, tooling, architecture, generated contracts, integration and repository-exam checks. The broader Make diagnostic flow and canonical `make certify` retain their separate meanings; passing diagnostics alone is not APP PASS.

**Outputs and failures:** native checks, Run/Step evidence and Game Tape preserve their actual results. A failed, unavailable or interrupted check stays unverified. No model statement or diagnostic result substitutes for canonical certification.

**Customize:** change the owning repository contract and focused regressions; do not copy this internal Plan into a different product. See the [execution/certification contract](../../../../docs/CI_CD_CONTRACT.md).
