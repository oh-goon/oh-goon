from __future__ import annotations

import re
from typing import Annotated, Literal

from cryptography import x509
from pydantic import Field, field_validator, model_validator

from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.models.base import ImmutableModel

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DNS_SUBDOMAIN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_API_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_DIGEST_IMAGE = re.compile(
    r"^(?P<registry>(?=.{1,259}/)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?::\d{1,5})?)/"
    r"(?P<repository>[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*)"
    r"@sha256:[0-9a-f]{64}$"
)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
KUBERNETES_SET_IMAGE_OUTPUT_NAMES = (
    "KUBERNETES_RESOURCE_VERSION",
    "KUBERNETES_GENERATION",
    "KUBERNETES_IMAGE",
)


def _validate_dns_label(value: str, *, label: str) -> str:
    if _DNS_LABEL.fullmatch(value) is None:
        raise ValueError(f"Fleet Kubernetes {label} must be one canonical DNS label")
    return value


def _validate_dns_subdomain(value: str, *, label: str) -> str:
    if _DNS_SUBDOMAIN.fullmatch(value) is None or ".." in value:
        raise ValueError(f"Fleet Kubernetes {label} must be one canonical DNS subdomain")
    return value


def is_digest_image_reference(value: str) -> bool:
    if _DIGEST_IMAGE.fullmatch(value) is not None:
        match = _DIGEST_IMAGE.fullmatch(value)
        assert match is not None
        registry = match.group("registry")
        if ":" in registry:
            port = int(registry.rsplit(":", 1)[1])
            if port < 1 or port > 65535:
                return False
        return True
    return False


def is_digest_image_or_output_reference(value: str) -> bool:
    return is_digest_image_reference(value) or step_output_reference(value) is not None


def validate_kubernetes_bearer_token(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._~+/=-]{16,8192}", value) is None:
        raise ValueError(
            "Fleet Kubernetes bearer token must be non-empty and use the admitted safe token alphabet"
        )
    return value


def validate_kubernetes_ca_certificate(value: str) -> str:
    if "\x00" in value or not value.startswith("-----BEGIN CERTIFICATE-----"):
        raise ValueError("Fleet Kubernetes CA certificate must be non-empty PEM certificate data")
    if not value.rstrip().endswith("-----END CERTIFICATE-----"):
        raise ValueError("Fleet Kubernetes CA certificate PEM is incomplete")
    try:
        certificates = x509.load_pem_x509_certificates(value.encode("utf-8"))
    except ValueError as exc:
        raise ValueError(
            "Fleet Kubernetes CA certificate must contain valid X.509 PEM data"
        ) from exc
    if not certificates:
        raise ValueError("Fleet Kubernetes CA certificate contains no X.509 certificates")
    return value


class AuthoringKubernetesApiEndpoint(ImmutableModel):
    hostname: str = Field(min_length=3, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str) -> str:
        if "${{" in value:
            return value
        KubernetesApiEndpoint(hostname=value, port=443)
        return value


class KubernetesApiEndpoint(ImmutableModel):
    hostname: str = Field(min_length=3, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str) -> str:
        if value != value.lower() or value.endswith(".") or _API_HOSTNAME.fullmatch(value) is None:
            raise ValueError(
                "Fleet Kubernetes API hostname must be a canonical lowercase DNS name with a dot"
            )
        return value

    @property
    def authority(self) -> str:
        return self.hostname if self.port == 443 else f"{self.hostname}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"https://{self.authority}"


class AuthoringKubernetesApiCredentials(ImmutableModel):
    token_env: str = Field(pattern=_ENV_NAME.pattern)
    ca_certificate_env: str = Field(pattern=_ENV_NAME.pattern)

    @model_validator(mode="after")
    def validate_distinct_names(self) -> AuthoringKubernetesApiCredentials:
        if self.token_env == self.ca_certificate_env:
            raise ValueError("Fleet Kubernetes token and CA certificate env names must be distinct")
        return self


class KubernetesApiCredentials(ImmutableModel):
    token_host_env: str = Field(pattern=_ENV_NAME.pattern)
    ca_certificate_host_env: str = Field(pattern=_ENV_NAME.pattern)

    @model_validator(mode="after")
    def validate_distinct_names(self) -> KubernetesApiCredentials:
        if self.token_host_env == self.ca_certificate_host_env:
            raise ValueError(
                "Fleet Kubernetes token and CA certificate host env names must be distinct"
            )
        return self

    @property
    def host_environment_names(self) -> tuple[str, str]:
        return self.token_host_env, self.ca_certificate_host_env


class AuthoringKubernetesDeploymentTarget(ImmutableModel):
    api: AuthoringKubernetesApiEndpoint
    namespace: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=253)
    container: str = Field(min_length=1, max_length=63)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        if "${{" in value:
            return value
        return _validate_dns_label(value, label="namespace")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if "${{" in value:
            return value
        return _validate_dns_subdomain(value, label="Deployment name")

    @field_validator("container")
    @classmethod
    def validate_container(cls, value: str) -> str:
        if "${{" in value:
            return value
        return _validate_dns_label(value, label="container name")


class KubernetesDeploymentTarget(ImmutableModel):
    api: KubernetesApiEndpoint
    namespace: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=253)
    container: str = Field(min_length=1, max_length=63)

    _namespace = field_validator("namespace")(
        lambda value: _validate_dns_label(value, label="namespace")
    )
    _name = field_validator("name")(
        lambda value: _validate_dns_subdomain(value, label="Deployment name")
    )
    _container = field_validator("container")(
        lambda value: _validate_dns_label(value, label="container name")
    )

    @property
    def resource_url(self) -> str:
        return (
            f"{self.api.base_url}/apis/apps/v1/namespaces/{self.namespace}/deployments/{self.name}"
        )


class ObserveCurrentKubernetesImage(ImmutableModel):
    mode: Literal["observe"] = "observe"


class ExactCurrentKubernetesImage(ImmutableModel):
    mode: Literal["exact"] = "exact"
    image: str = Field(min_length=1, max_length=512)

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        if any(character.isspace() for character in value) or "\x00" in value:
            raise ValueError(
                "Fleet Kubernetes exact current image cannot contain whitespace or NUL"
            )
        return value


KubernetesImagePrecondition = Annotated[
    ObserveCurrentKubernetesImage | ExactCurrentKubernetesImage,
    Field(discriminator="mode"),
]


class KubernetesSetImagePolicy(ImmutableModel):
    mutation: Literal["json_patch"] = "json_patch"
    test_resource_version: Literal[True] = True
    test_current_image: Literal[True] = True
    tls_verify: Literal[True] = True
    transport_retries: Literal[0] = 0
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)
    if_already_desired: Literal["succeed", "fail"] = "succeed"
    wait_for_rollout: Literal[False] = False


class KubernetesRolloutVerifyPolicy(ImmutableModel):
    """Explicit bounded observation policy; this is not an implicit deploy wait."""

    tls_verify: Literal[True] = True
    transport_retries: Literal[0] = 0
    request_timeout_seconds: int = Field(default=10, ge=1, le=60)
    poll_interval_seconds: float = Field(default=5.0, ge=0.25, le=60)
    require_progress_deadline: Literal[True] = True
    fail_if_superseded: Literal[True] = True


class KubernetesSetImageOutput(ImmutableModel):
    metadata: str = Field(default="kubernetes/deployment-image.json", min_length=1, max_length=240)
    resource_version: str = Field(
        default="kubernetes/resource-version.txt", min_length=1, max_length=240
    )
    generation: str = Field(default="kubernetes/generation.txt", min_length=1, max_length=240)
    image: str = Field(default="kubernetes/image-reference.txt", min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_distinct_paths(self) -> KubernetesSetImageOutput:
        values = (self.metadata, self.resource_version, self.generation, self.image)
        if len(values) != len(set(values)):
            raise ValueError("Fleet Kubernetes set-image output paths must be distinct")
        return self
