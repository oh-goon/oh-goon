# Template input inspection — adversarial review (GEN45_M142)

Status: working candidate, not release qualification or proof of complete security closure.

## Single authority

`FleetPlanResolver.analyze_source()` captures the source graph; `materialize_projected_inputs()` owns definitions, forwarding compatibility, and admission-invalid projection errors. `inspect_projected_inputs()` may only expose those definitions and traverse the analyzed graph to report owners. It must not read values, resolve external sources, execute workloads, or invent an independent contract.

## Attack inventory

| Attack | Expected behavior | Evidence |
| --- | --- | --- |
| Public catalog input parity | Inspect definitions equal canonical materialization for every shipped Plan | `test_every_shipped_plan_inspection_matches_canonical_materialization` |
| Unowned projected inputs | Fail before work in canonical materialization | `fleet_plan_input_resolution.py`: no-owner rejection; full native regression still required |
| Direct ambiguous owners | Fail rather than selecting a winner | `fleet_plan_input_resolution.py`: `len(matches) != 1`; full native regression still required |
| Composite incompatible forwarding | Reject before exposing a projected contract | `fleet_plan_input_resolution.py`: full-contract equality; full native regression still required |
| Invented root ownership | Nonprojected authored root inputs report no Goon owner | `test_nonprojected_root_inputs_have_no_invented_goon_owner` |
| Repeat inspection | Stable ordering and unchanged source bytes | `test_inspection_is_repeatable_and_does_not_mutate_source_files` |
| Symlink/path alias and same-path replacement | Must fail against canonical path/source authority | Existing source graph/containment tests, not rerun as part of this focused set |
| External source reads / executor effects | Must not occur during inspection | Implementation review only; integration interception still required |

## Evidence and boundaries

Focused inspection tests pass in a local environment using `PYTHONPATH=backend/src pytest` (4 passed). Locked offline `uv` verification could not initialize because Hashmarks v0.25.1 wheel was not cached. No native WSL, integration, release certification, or complete adversarial harness qualification is claimed. Analyzed source code produced no reproduced defect for the focused catalog parity and stability attacks; this is not a finding of comprehensive absence of defects.

Do not seal. Continue with one campaign-level qualification/release closure checkpoint, retaining blocking unknowns until authoritative native receipts exist. Every reproduced defect requires a focused regression and fresh changed-byte checkpoint before renewed qualification.
