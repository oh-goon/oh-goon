"""Built-in Oh-Goon repository-operation policy defaults and environment defaults."""

from __future__ import annotations

from types import MappingProxyType

DEFAULT_EXCLUSION_REASON = "Excluded by user"


def default_exclusion_reason() -> str:
    """Return the single authored exclusion default or fail closed if it is invalid."""
    value = DEFAULT_EXCLUSION_REASON
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 500:
        raise RuntimeError("default exclusion reason policy is malformed")
    return value


DEFAULT_FLEET_PARALLELISM = 4

DEFAULT_ENGINE_ENV = MappingProxyType(
    {
        "OH_GOON_REQUIRE_REMOTE": "true",
        "OH_GOON_TEST_COMMAND": "uv run --frozen pytest",
        "OH_GOON_REQUIRE_AUDIT": "false",
        "OH_GOON_FAIL_ON_AUDIT": "true",
        "OH_GOON_AUDIT_TIMEOUT": "20",
        "OH_GOON_GIT_TIMEOUT": "30",
        "OH_GOON_MALWARE_CHECK": "false",
        "OH_GOON_WORKSPACE_MODE": "root",
        "OH_GOON_REPORTS": "true",
        "OH_GOON_REPORT_HISTORY": "false",
        "OH_GOON_REQUIRE_PYTHON_PIN": "false",
    }
)
