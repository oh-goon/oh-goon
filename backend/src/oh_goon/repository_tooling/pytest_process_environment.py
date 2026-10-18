from __future__ import annotations

import os


def isolated_pytest_environment() -> dict[str, str]:
    """Return subprocess env with ambient pytest entry-point plugins disabled."""

    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env
