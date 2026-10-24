# oh-goon/backend-tests

Run the complete bounded canonical backend Python qualification suite.

**Status:** internal owner command. **Effects:** Runs the repository-owned native command and records verification/release evidence.

**Prerequisites:** This repository’s prepared checkout. `make init` owns dependency materialization once; the owning Make/certification lifecycle consumes that environment.

**What to edit / settings owner:** the repository owns this internal package and its native command. Use its owning Make/certification route; do not initialize it as a portable user workflow. `source_workspace` inputs, when declared, are typed earlier-producer references and never host paths.

See the [internal Plan guide](../../../plans/oh-goon/app-certification/README.md) and [self-hosting boundaries](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).

**Expected outputs:** The owning command’s actual result and declared evidence. A diagnostic does not grant APP certification.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

This Goon owns orchestration/evidence only. The repository-owned `make test-python-complete` command owns test semantics and hard timeout policy; no alternate implementation is introduced.

The strict backend fixtures intentionally use `ldd` as their runtime-dependency inspector and construct inner Plans whose primary command is `true`. Make/script analysis derives both facts from the repository-owned workload. `backend/goon-execution-requirements.json` independently permits the `make:test-python-complete` workload need without restating this Goon's Step semantics, and the generated canonical tool-authority projection transports the exact admitted set into the Step. The Goon YAML does not repeat `ldd`, `true`, or other derived native tools. Fleet still freezes the exact executables into `/toolchain/bin`, so the tests cannot fall through to ambient host tooling.
