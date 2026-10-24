# oh-goon/tooling-tests

Run the complete canonical repository tooling test suite.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**What to edit / settings owner:** the repository owns this internal package and its native command. Use its owning Make/certification route; do not initialize it as a portable user workflow. `source_workspace` inputs, when declared, are typed earlier-producer references and never host paths.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

This Goon owns orchestration/evidence only. The existing repository command owns test semantics; no parallel test implementation or alternate lifecycle is introduced.

The complete tooling suite also owns `bash` explicitly because `scripts/tests/test_run_logging.py` launches nested POSIX shell processes dynamically through Python `subprocess`; this child edge is not derivable from Make recipe syntax and is therefore declared as an opaque workload tool.

Shell authority (`bash`): Nested subprocess tests in test_run_logging.py require admitted bash tool authority, independent of the direct make argv.
