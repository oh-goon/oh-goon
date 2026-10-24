from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_migration import GitLabResolvedComponent


class GitLabHttpResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...


class GitLabComponentTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> GitLabHttpResponse: ...


class GitLabComponentTransportError(RuntimeError):
    """Transport-owned fetch failure normalized away from the HTTP client library."""


class Httpx2GitLabComponentTransport:
    """Leaf adapter for the required httpx2 runtime dependency.

    The import is deliberately deferred until network fetch is actually requested. Merely
    importing GoonService/API dependencies must not require the GitLab HTTP transport; this
    keeps unrelated unit/runtime tests runnable before the dependency-complete APP job.
    """

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> GitLabHttpResponse:
        import httpx2

        try:
            return httpx2.get(url, params=params, headers=headers, timeout=timeout)
        except httpx2.HTTPError as exc:
            raise GitLabComponentTransportError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class GitLabComponentSource:
    metadata: GitLabResolvedComponent
    content: str


@dataclass(frozen=True, slots=True)
class GitLabComponentFetcher:
    """Fetch one pinned GitLab CI component during migration only."""

    transport: GitLabComponentTransport = field(default_factory=Httpx2GitLabComponentTransport)

    def fetch(self, reference: str, *, token: str | None = None) -> GitLabComponentSource:
        host, project, component, revision = parse_component_reference(reference)
        headers = {"PRIVATE-TOKEN": token} if token else {}
        errors: list[str] = []
        for source_path in (f"templates/{component}.yml", f"templates/{component}/template.yml"):
            project_id = quote(project, safe="")
            file_id = quote(source_path, safe="")
            url = f"https://{host}/api/v4/projects/{project_id}/repository/files/{file_id}/raw"
            try:
                response = self.transport.get(
                    url, params={"ref": revision}, headers=headers, timeout=20.0
                )
            except GitLabComponentTransportError as exc:
                errors.append(f"{source_path}: {exc}")
                continue
            if response.status_code == 200:
                content = response.text
                return GitLabComponentSource(
                    metadata=GitLabResolvedComponent(
                        reference=reference,
                        host=host,
                        project=project,
                        component=component,
                        revision=revision,
                        source_path=source_path,
                        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    ),
                    content=content,
                )
            errors.append(f"{source_path}: HTTP {response.status_code}")
        detail = "; ".join(errors)
        raise FleetPlanInvalid(f"Cannot fetch GitLab component {reference}: {detail}")


def parse_component_reference(reference: str) -> tuple[str, str, str, str]:
    if "@" not in reference:
        raise FleetPlanInvalid(
            f"GitLab component reference must pin a version or SHA with @: {reference}"
        )
    location, revision = reference.rsplit("@", 1)
    parts = location.split("/")
    if len(parts) < 3 or not all(parts):
        raise FleetPlanInvalid(
            "GitLab component reference must be <host>/<project-path>/<component>@<revision>"
        )
    if (
        not revision
        or revision.startswith("~")
        or (revision.count(".") in {0, 1} and revision.isdigit())
    ):
        raise FleetPlanInvalid(
            f"GitLab component import requires an exact release/tag/SHA, not a moving selector: {revision}"
        )
    host = parts[0]
    project = "/".join(parts[1:-1])
    component = parts[-1]
    return host, project, component, revision
