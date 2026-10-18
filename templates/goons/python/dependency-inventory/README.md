# python/dependency-inventory

**Production template.**

Read-only Python dependency observation for fleet discovery.

The Goon parses repository dependency files as data and publishes one **complete typed dependency snapshot** into canonical Run/Step evidence. It does not modify dependencies, contact package indexes, execute repository code, classify repository health, or decide which maintenance Plan should run next.

A successful snapshot covers the root `pyproject.toml`, `uv.lock`, and discovered uv workspace member `pyproject.toml` files. Resolved lock versions remain distinct from declared dependency ranges. If required sources cannot be parsed, the Step fails and no replacement observation snapshot becomes current.
