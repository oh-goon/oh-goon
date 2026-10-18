# oh-goon/backend-tests

Run the complete bounded canonical backend Python qualification suite.

This Goon owns orchestration/evidence only. The repository-owned `make test-python-complete` command owns test semantics and hard timeout policy; no alternate implementation is introduced.

The strict backend fixtures intentionally use `ldd` as their runtime-dependency inspector and construct inner Plans whose primary command is `true`. Make/script analysis derives both facts from the repository-owned workload. `backend/goon-execution-requirements.json` independently permits the `make:test-python-complete` workload need without restating this Goon's Step semantics, and the generated canonical tool-authority projection transports the exact admitted set into the Step. The Goon YAML does not repeat `ldd`, `true`, or other derived native tools. Fleet still freezes the exact executables into `/toolchain/bin`, so the tests cannot fall through to ambient host tooling.
