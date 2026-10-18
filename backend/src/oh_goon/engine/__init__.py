from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oh_goon.engine.runner import EngineRunner as EngineRunner

__all__ = ["EngineRunner"]


def __getattr__(name: str) -> Any:
    """Keep package import side-effect free while preserving the public EngineRunner export."""

    if name == "EngineRunner":
        from oh_goon.engine.runner import EngineRunner

        return EngineRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
