from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[4]


def isolated_pytest_environment(
    base: Mapping[str, str] | None = None, *, qualified: bool = False
) -> dict[str, str]:
    """Preserve qualified native plugins; isolate ambient plugins in default batches."""

    env = dict(os.environ if base is None else base)
    if not qualified:
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


@dataclass(frozen=True, slots=True)
class PytestInvocation:
    cwd: Path
    source_root: Path
    config_file: Path
    pytest_args: tuple[str, ...]
    command_prefix: tuple[str, ...]
    environment: Mapping[str, str]
    qualified: bool
    config_identity: str

    def correctness_context(self) -> str:
        def source_location(path: Path) -> str:
            if path.is_relative_to(self.source_root):
                return path.relative_to(self.source_root).as_posix()
            return path.as_posix()

        payload = {
            "cwd": source_location(self.cwd),
            "config_file": source_location(self.config_file),
            "config_identity": self.config_identity,
            "pytest_args": self.pytest_args,
            "qualified": self.qualified,
            "pytest_environment": {
                name: self.environment.get(name)
                for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "native-pytest-invocation:" + hashlib.sha256(encoded).hexdigest()


def resolve_pytest_invocation(
    *,
    qualified: bool = False,
    pytest_config: Path = Path("backend/pyproject.toml"),
    pytest_args: tuple[str, ...] = (),
) -> PytestInvocation:
    cwd = Path.cwd().resolve()
    config = (cwd / pytest_config).resolve()
    from oh_goon.core.file_digest import sha256_file

    try:
        config_identity = sha256_file(config)
    except OSError:
        config_identity = "missing"
    prefix: tuple[str, ...]
    if qualified:
        from oh_goon.repository_tooling.qualification_exec import (
            _clean_environment,
            _require_bound_interpreter,
        )

        interpreter = str(_require_bound_interpreter())
        base = _clean_environment()
        prefix = (interpreter, "-I", "-m", "pytest")
    else:
        base = dict(os.environ)
        prefix = (sys.executable, "-m", "pytest")
    return PytestInvocation(
        cwd,
        ROOT,
        config,
        pytest_args,
        prefix,
        MappingProxyType(isolated_pytest_environment(base, qualified=qualified)),
        qualified,
        config_identity,
    )
