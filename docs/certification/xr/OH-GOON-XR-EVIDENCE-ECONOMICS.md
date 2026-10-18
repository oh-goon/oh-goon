# XR evidence economics / CL-01 bridge

Status: CLOSED by Oh-Goon 1267.0.209 CL-01 operation economics instrumentation.

The dependency-supply path now records actual bytes hashed/read/written/copied/reused and strong-proof performed/reused state at owner boundaries. Representative cold/warm evidence showed warm retained binding can avoid copying while still performing repeated full strong content proof. This is the measured input for CL-07/CL-08.

The canonical Hashmarks artifact rebase does not change those economics semantics and must remain separate from CL-07/CL-08 optimization.
