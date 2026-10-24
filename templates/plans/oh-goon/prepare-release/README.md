# oh-goon/prepare-release

Package the exact source authority that already produced canonical APP PASS.

**Status:** internal owner command. Use this repository’s owner command rather than initializing a portable user workflow.

**Effects and prerequisites:** uses the prepared Oh-Goon checkout and its declared native commands. Run `make init` once to materialize the committed dependencies; verification consumes that environment. Release packaging additionally requires its strict executor/content store and APP PASS for the exact source.

```sh
make prepare-release
```

Version authoring is an earlier explicit `make release-version-set OH_GOON_RELEASE_VERSION=<x.y.z>` operation. This Plan does not restamp or normalize certified source. It retains the exact source ZIP and digest/manifest; independent artifact certification must read back those retained bytes as a fresh candidate.

**Outputs and failures:** native checks, Run/Step evidence and Game Tape preserve their actual results. A failed, unavailable or interrupted check stays unverified. No model statement or diagnostic result substitutes for canonical certification.

**Customize:** change the owning repository contract and focused regressions; do not copy this internal Plan into a different product. See the [execution/certification contract](../../../../docs/CI_CD_CONTRACT.md).
