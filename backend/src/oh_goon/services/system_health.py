from collections.abc import Callable

from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.models.tool_health import (
    BrokenToolHealth,
    MissingOptionalToolHealth,
    MissingRequiredToolHealth,
    ReadyToolHealth,
    SystemHealth,
    ToolHealth,
)
from oh_goon.core.vocabulary import ToolStatus
from oh_goon.engine.command import run_command
from oh_goon.services.runtime_safety import RuntimeSafetyService


class SystemHealthService:
    def __init__(
        self,
        runtime_safety: RuntimeSafetyService,
        background_failures: Callable[[], list[BackgroundFailure]],
    ) -> None:
        self._runtime_safety = runtime_safety
        self._background_failures = background_failures

    REQUIRED = ("git", "uv")
    OPTIONAL: tuple[str, ...] = ()

    def inspect(self) -> SystemHealth:
        tools = [self._inspect_tool(name, required=True) for name in self.REQUIRED]
        tools.extend(self._inspect_tool(name, required=False) for name in self.OPTIONAL)
        proof = self._runtime_safety.refresh()
        return SystemHealth(
            tools=tools,
            runtime_safety=proof,
            background_failures=self._background_failures(),
        )

    def _inspect_tool(self, name: str, *, required: bool) -> ToolHealth:
        try:
            result = run_command(name, "--version")
        except OSError:
            return self._missing(name, required=required)
        if result.returncode != 0:
            return BrokenToolHealth(
                name=name,
                required=required,
                status=ToolStatus.BROKEN,
                detail=result.stderr or None,
            )
        return ReadyToolHealth(
            name=name, required=required, status=ToolStatus.READY, version=result.stdout
        )

    @staticmethod
    def _missing(name: str, *, required: bool) -> ToolHealth:
        if required:
            return MissingRequiredToolHealth(name=name, status=ToolStatus.MISSING)
        return MissingOptionalToolHealth(name=name, status=ToolStatus.OPTIONAL_MISSING)
