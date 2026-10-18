# ADR: Isolated native qualification must collect without incidental sys.path state

**Checkpoint:** CO-R2c-Q1. **Status:** development-only repair.

The exact CO-R2W3 native WSL evidence showed 849 selected tests passing in five
rings but CORE failing collection. `test_co_r2w3_git_retirement.py` imported its
real Git setup and offline identity fixture from a sibling test using an
unqualified `test_co_r2w2_native_git_recovery` module name. CORE's isolated
selection did not add the sibling test directory to `sys.path`, resulting in
`ModuleNotFoundError` before any Git retirement test ran.

Decision: Use the existing Python test package's full module path
`tests.unit.runtime.test_co_r2w2_native_git_recovery`, not a new runtime or Git
helper owner. Add an independently spawned `pytest --collect-only` regression
for the exact native CORE target. Keep the QENV1 canonical selector unchanged
except registering this new regression in the existing CORE ring. No change
to native runtime authority, engine operation, workspace retirement or physical
LACE claims.

The imported helpers are **tests only**; the existing original Git and database
owners retain authority. Real Git retirement, process-crash safety, WSL kernel
behavior and evidence custody must be requalified on this exact repaired source.
The earlier failed CORE does not become a pass retroactively. The successful
AGENT_PREWORK/B6/B7/PHYSICAL_OBSERVER rings remain observations of the older
CO-R2W3 source, not certification of this source.
