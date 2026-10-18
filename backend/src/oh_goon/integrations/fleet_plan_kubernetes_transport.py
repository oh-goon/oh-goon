from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from oh_goon.core.models.fleet_plan_kubernetes import (
    KubernetesApiCredentials,
    KubernetesDeploymentTarget,
    validate_kubernetes_bearer_token,
    validate_kubernetes_ca_certificate,
)
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.exact_file_write import publish_text_file
from oh_goon.integrations.fleet_plan_private_root import (
    prepare_attempt_private_root,
    remove_attempt_private_root,
)


class KubernetesCommandRunner(Protocol):
    def __call__(
        self,
        *args: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class KubernetesPrivateConfig:
    root: Path
    auth_config: Path
    ca_certificate: Path


def prepare_kubernetes_private_config(
    paths: AttemptPathLayout,
    credentials: KubernetesApiCredentials,
    host_environment: Mapping[str, str],
    *,
    owner: str,
) -> KubernetesPrivateConfig:
    """Materialize only the declared Kubernetes credentials inside one attempt-private root."""

    token = host_environment[credentials.token_host_env]
    ca_certificate = host_environment[credentials.ca_certificate_host_env]
    validate_kubernetes_bearer_token(token)
    validate_kubernetes_ca_certificate(ca_certificate)

    root = prepare_attempt_private_root(paths.config / owner)
    try:
        ca_path = root / "ca.crt"
        publish_text_file(ca_path, ca_certificate.rstrip() + "\n")
        ca_path.chmod(0o600)
        auth_config = root / "curl-auth.conf"
        publish_text_file(
            auth_config,
            'header = "Authorization: Bearer ' + _curl_config_escape(token) + '"\n'
            'cacert = "' + _curl_config_escape(str(ca_path)) + '"\n',
        )
        auth_config.chmod(0o600)
        return KubernetesPrivateConfig(root=root, auth_config=auth_config, ca_certificate=ca_path)
    except BaseException as exc:
        try:
            remove_attempt_private_root(root)
        except OSError as cleanup_exc:
            raise OSError(
                f"Fleet Kubernetes private config cleanup failed after preparation error: {cleanup_exc}"
            ) from exc
        raise


def run_kubernetes_request(
    target: KubernetesDeploymentTarget,
    config: KubernetesPrivateConfig,
    *,
    command_runner: KubernetesCommandRunner,
    environment: Mapping[str, str],
    cancel_requested: Callable[[], bool] | None,
    request_timeout_seconds: int,
    transport_retries: int,
    step_timeout_seconds: float | None,
    method: str,
    output: Path,
    body: Path | None = None,
    content_type: str | None = None,
) -> CommandResult:
    """Run one explicit HTTPS request without ambient proxy/TLS authority."""

    output.unlink(missing_ok=True)
    argv = [
        "curl",
        "--disable",
        "--config",
        str(config.auth_config),
        "--proto",
        "=https",
        "--tlsv1.2",
        "--retry",
        str(transport_retries),
        "--connect-timeout",
        str(min(10, request_timeout_seconds)),
        "--max-time",
        str(request_timeout_seconds),
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--request",
        method,
        "--header",
        "Accept: application/json",
    ]
    if body is not None:
        if not content_type:
            raise ValueError("Fleet Kubernetes request body requires an explicit content type")
        argv.extend(("--header", f"Content-Type: {content_type}", "--data-binary", f"@{body}"))
    argv.extend(("--output", str(output), "--write-out", "%{http_code}", target.resource_url))
    return command_runner(
        *argv,
        cwd=config.root,
        timeout=step_timeout_seconds,
        cancel_requested=cancel_requested,
        env=kubernetes_environment(environment),
    )


def kubernetes_http_status(result: CommandResult) -> int:
    value = result.stdout.strip()
    return int(value) if len(value) == 3 and value.isdigit() else 0


def kubernetes_environment(environment: Mapping[str, str]) -> dict[str, str]:
    # Kubernetes transport trust and routing are attempt-owned. Environment names
    # are case-insensitive on Windows, so filtering only canonical spellings lets
    # differently-cased ambient proxy/CA authority survive into curl.
    transport_owned = {
        name.casefold()
        for name in (
            "CURL_CA_BUNDLE",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        )
    }
    return {
        name: value for name, value in environment.items() if name.casefold() not in transport_owned
    }


def cleanup_kubernetes_private_config(config: KubernetesPrivateConfig) -> None:
    """Remove the whole attempt-private transport root, including bearer-token material."""

    remove_attempt_private_root(config.root)


def _curl_config_escape(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("Fleet Kubernetes curl config values cannot contain newlines")
    return value.replace("\\", "\\\\").replace('"', '\\"')
