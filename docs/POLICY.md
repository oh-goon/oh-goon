# Policy

Each operation captures one immutable `PolicySnapshot` containing the effective values, their provenance sources and deterministic `policy_id`. Policy precedence is resolved in Python and reported with every operation.

Do not duplicate policy defaults in adapters or UI code. Add/modify canonical defaults and precedence in the core policy owner and extend table-driven tests.
