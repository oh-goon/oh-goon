from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.launch_manifest import LaunchEndpointIntent, LaunchIntent, LaunchServiceIntent


@dataclass(frozen=True, slots=True)
class LocalServicePlan:
    service: str
    generation: int
    command: tuple[str, ...]
    cwd: Path | None
    host: str
    port: int
    readiness_path: str
    readiness_status: int

    @property
    def readiness_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.readiness_path}"


@dataclass(frozen=True, slots=True)
class LocalLaunchPlan:
    profile: str
    services: tuple[LocalServicePlan, ...]
    startup_timeout_seconds: float = 30.0

    def intent(self) -> LaunchIntent:
        return LaunchIntent(
            profile=self.profile,
            services=tuple(
                LaunchServiceIntent(
                    service=service.service,
                    command=service.command,
                    endpoint=LaunchEndpointIntent(
                        service=service.service,
                        host=service.host,
                        port=service.port,
                        exposure="local",
                    ),
                )
                for service in self.services
            ),
        )


def built_local_plan(*, host: str = "127.0.0.1", port: int = 8787) -> LocalLaunchPlan:
    return LocalLaunchPlan(
        profile="built-local",
        services=(
            LocalServicePlan(
                service="backend",
                generation=1,
                command=(
                    "uvicorn",
                    "oh_goon.api.app:app",
                    "--host",
                    host,
                    "--port",
                    str(port),
                ),
                cwd=None,
                host=host,
                port=port,
                readiness_path="/healthz",
                readiness_status=204,
            ),
        ),
    )


def contributor_dev_plan(root: Path) -> LocalLaunchPlan:
    root = root.resolve()
    return LocalLaunchPlan(
        profile="contributor-dev",
        services=(
            LocalServicePlan(
                service="backend",
                generation=1,
                command=(
                    str(root / "backend/.venv/bin/oh-goon-local-dev-backend"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8787",
                    "--reload",
                    "--reload-dir",
                    "backend/src",
                ),
                cwd=root,
                host="127.0.0.1",
                port=8787,
                readiness_path="/healthz",
                readiness_status=204,
            ),
            LocalServicePlan(
                service="frontend",
                generation=1,
                command=("npm", "run", "dev"),
                cwd=root / "frontend",
                host="127.0.0.1",
                port=5173,
                readiness_path="/",
                readiness_status=200,
            ),
        ),
    )
