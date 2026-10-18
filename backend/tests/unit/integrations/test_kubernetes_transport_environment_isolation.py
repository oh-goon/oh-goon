from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.fleet_plan_kubernetes import KubernetesApiEndpoint, KubernetesDeploymentTarget
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.fleet_plan_kubernetes_transport import KubernetesPrivateConfig, run_kubernetes_request


def test_kubernetes_request_cannot_inherit_case_variant_proxy_or_ca_authority(tmp_path: Path) -> None:
    observed: dict[str, str] = {}

    def runner(*argv: str, **kwargs) -> CommandResult:
        observed.update(kwargs["env"])
        return CommandResult(returncode=0, stdout="200", stderr="", argv=tuple(argv))

    target = KubernetesDeploymentTarget(
        api=KubernetesApiEndpoint(hostname="kube.example.com"),
        namespace="production",
        name="api",
        container="api",
    )
    config = KubernetesPrivateConfig(
        root=tmp_path,
        auth_config=tmp_path / "curl-auth.conf",
        ca_certificate=tmp_path / "ca.crt",
    )
    run_kubernetes_request(
        target,
        config,
        command_runner=runner,
        environment={
            "PATH": "/toolchain/bin",
            "Https_Proxy": "http://host-proxy.invalid",
            "Ssl_Cert_File": "/host/untrusted-ca.pem",
        },
        cancel_requested=None,
        request_timeout_seconds=5,
        transport_retries=0,
        step_timeout_seconds=5,
        method="GET",
        output=tmp_path / "response.json",
    )

    assert observed == {"PATH": "/toolchain/bin"}
