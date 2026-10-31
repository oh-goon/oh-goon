from collections.abc import Callable
from subprocess import TimeoutExpired

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
from oh_goon.engine.command import (
    CommandOutputLimitExceeded,
    CommandSupervisionTerminated,
    run_command,
)
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
        tools = self.inspect_tools()
        proof = self._runtime_safety.refresh()
        return SystemHealth(
            tools=tuple(tools),
            runtime_safety=proof,
            background_failures=tuple(self._background_failures()),
        )

    @classmethod
    def inspect_tools(cls) -> tuple[ToolHealth, ...]:
        """Observe native CLI prerequisites without constructing application state."""
        return tuple(cls._inspect_tool(name, required=True) for name in cls.REQUIRED) + tuple(
            cls._inspect_tool(name, required=False) for name in cls.OPTIONAL
        )

    @classmethod
    def _inspect_tool(cls, name: str, *, required: bool) -> ToolHealth:
        try:
            result = run_command(
                name,
                "--version",
                timeout=5,
                max_capture_bytes_per_stream=8192,
                require_process_group_quiescence=True,
                parent_death_signal=True,
            )
        except OSError:
            return cls._missing(name, required=required)
        except (TimeoutExpired, CommandOutputLimitExceeded) as exc:
            return BrokenToolHealth(
                name=name,
                required=required,
                status=ToolStatus.BROKEN,
                detail=(
                    "Native version probe exceeded its deadline"
                    if isinstance(exc, TimeoutExpired)
                    else "Native version probe exceeded its output limit"
                ),
            )
        except CommandSupervisionTerminated:
            return BrokenToolHealth(
                name=name,
                required=required,
                status=ToolStatus.BROKEN,
                detail="Native version probe settlement is UNPROVEN",
            )
        if not result.supervisor.final_process_group_quiescent:
            return BrokenToolHealth(
                name=name,
                required=required,
                status=ToolStatus.BROKEN,
                detail="Native version probe settlement is UNPROVEN",
            )
        if result.returncode != 0:
            return BrokenToolHealth(
                name=name,
                required=required,
                status=ToolStatus.BROKEN,
                detail="Native version probe returned a nonzero exit status",
            )
        return ReadyToolHealth(
            name=name, required=required, status=ToolStatus.READY, version=result.stdout
        )

    @staticmethod
    def _missing(name: str, *, required: bool) -> ToolHealth:
        if required:
            return MissingRequiredToolHealth(name=name, status=ToolStatus.MISSING)
        return MissingOptionalToolHealth(name=name, status=ToolStatus.OPTIONAL_MISSING)
