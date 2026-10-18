# oh-goon/tooling-tests

Run the complete canonical repository tooling test suite.

This Goon owns orchestration/evidence only. The existing repository command owns test semantics; no parallel test implementation or alternate lifecycle is introduced.

The complete tooling suite also owns `bash` explicitly because `scripts/tests/test_run_logging.py` launches nested POSIX shell processes dynamically through Python `subprocess`; this child edge is not derivable from Make recipe syntax and is therefore declared as an opaque workload tool.

Shell authority (`bash`): Nested subprocess tests in test_run_logging.py require admitted bash tool authority, independent of the direct make argv.
