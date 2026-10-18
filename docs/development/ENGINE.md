# Native engine

`backend/src/oh_goon/engine/` is the single deterministic engine for GUI and CLI.

- `command.py`: typed cancellable subprocess execution
- `operation_execution_context.py`: immutable operation context
- `git.py`: Git inspection/fingerprint helpers
- `project.py`: project/policy inspection
- `reporting.py`: canonical report/provenance builders
- `operations/`: Init/Plan/Update/Verify/Prepare Review implementations plus focused command/readiness/validation owners
- `runner.py`: action dispatch into those focused operations

Do not add a shell or alternate compatibility engine. Tests live under `backend/tests/` and should fake the narrow command owner used by the behavior under test or use temporary Git repositories. Do not create a new aggregate `common.py`/`utils.py` seam.
