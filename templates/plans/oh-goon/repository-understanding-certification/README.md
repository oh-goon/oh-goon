# oh-goon/repository-understanding-certification

Verify the repository-understanding exam material and retain its manifest.

**Status:** internal owner command. Use this repository’s owner command rather than initializing a portable user workflow.

**Effects and prerequisites:** uses the prepared Oh-Goon checkout and its declared native commands. Run `make init` once to materialize the committed dependencies; verification consumes that environment.

```sh
make repository-understanding-exam
```

This verifies the question/answer bank and version-bound evidence. It does not execute or certify a candidate model. Grader-only answer truth must stay outside repository evidence delivered to a candidate.

**Outputs and failures:** native checks, Run/Step evidence and Game Tape preserve their actual results. A failed, unavailable or interrupted check stays unverified. No model statement or diagnostic result substitutes for canonical certification.

**Customize:** change the owning repository contract and focused regressions; do not copy this internal Plan into a different product. See the [execution/certification contract](../../../../docs/CI_CD_CONTRACT.md).
