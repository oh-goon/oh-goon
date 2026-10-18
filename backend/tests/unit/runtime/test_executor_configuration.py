from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_opensandbox_isolation import OpenSandboxProcessIsolation
from oh_goon.runtime._executor_configuration import configured_executor_pool
from oh_goon.runtime.database import LocalDatabase
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _portable_path_authority(text: str) -> str:
    """Keep parser fixtures inside the currently admitted backend-test tool authority.

    These tests validate executor-inventory semantics, not the ambient host's `/usr/bin`
    layout. APP certification intentionally hides ambient host directories, so a valid
    fixture must use the exact strict path authority admitted for this test Step.
    """

    legacy = (
        'runtime_inspector = "/usr/bin/ldd"\n'
        'trusted_host_roots = ["/usr/bin"]\n'
        'tool_search_paths = ["/usr/bin"]'
    )
    replacement = "\n".join(
        (
            f"runtime_inspector = {json.dumps(str(TEST_PATH_AUTHORITY.runtime_inspector))}",
            "trusted_host_roots = ["
            + ", ".join(json.dumps(str(path)) for path in TEST_PATH_AUTHORITY.trusted_roots)
            + "]",
            "tool_search_paths = ["
            + ", ".join(json.dumps(str(path)) for path in TEST_PATH_AUTHORITY.tool_search_paths)
            + "]",
        )
    )
    normalized = text.replace(legacy, replacement)
    legacy_single = (
        "runtime_inspector = '/usr/bin/ldd'\n"
        "trusted_host_roots = ['/usr/bin']\n"
        "tool_search_paths = ['/usr/bin']"
    )
    return normalized.replace(legacy_single, replacement)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "executors.toml"
    path.write_text(_portable_path_authority(text), encoding="utf-8")
    return path


def _remote_assets(tmp_path: Path, domain: str, runtime: str = "kata") -> tuple[Path, Path, Path]:
    cli = tmp_path / f"osb-{domain.replace('.', '-')}"
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli.chmod(0o755)
    private_key = Ed25519PrivateKey.generate()
    public_key = tmp_path / f"attestation-{domain.replace('.', '-')}.pub.pem"
    public_key.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    claims = {
        "schema": "oh-goon.opensandbox-runtime-attestation.v1",
        "domain": domain,
        "protocol": "https",
        "secure_runtime": runtime,
        "server_configuration_sha256": "a" * 64,
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    attestation = tmp_path / f"attestation-{domain.replace('.', '-')}.json"
    attestation.write_text(
        json.dumps(
            {
                "claims": claims,
                "signature": base64.b64encode(private_key.sign(canonical)).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return cli, attestation, public_key


def test_executor_inventory_supports_local_and_multiple_remote_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_a = "OH_GOON_TEST_OS_A"
    key_b = "OH_GOON_TEST_OS_B"
    monkeypatch.setenv(key_a, "secret-a")
    monkeypatch.setenv(key_b, "secret-b")
    cli_a, attestation_a, public_key_a = _remote_assets(tmp_path, "sandbox-a.example")
    cli_b, attestation_b, public_key_b = _remote_assets(tmp_path, "sandbox-b.example")
    path = _write(
        tmp_path,
        f'''version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "local"
generation = 1
execution = {{ environment = {{ backend = "bubblewrap" }} }}
bwrap_executable = "/usr/bin/bwrap"
priority = 10
cpu_millis = 8000
memory_mib = 16384
max_processes = 2048
cgroup_root = "/sys/fs/cgroup/oh-goon"
egresS_pool = "10.253.0.0/24"
ip_executable = "/usr/sbin/ip"
nft_executable = "/usr/sbin/nft"

[[executors]]
id = "kata-a"
generation = 1
execution = {{ environment = {{ backend = "opensandbox" }} }}
priority = 20
cpu_millis = 16000
memory_mib = 32768
domain = "sandbox-a.example"
protocol = "https"
cli_executable = "{cli_a}"
runtime_attestation_file = "{attestation_a}"
runtime_attestation_public_key_file = "{public_key_a}"
image = "registry.example/fleet-runner@sha256:{"1" * 64}"
runtime = "kata"
api_key_env = "{key_a}"

[[executors]]
id = "kata-b"
generation = 1
execution = {{ environment = {{ backend = "opensandbox" }} }}
priority = 20
cpu_millis = 16000
memory_mib = 32768
domain = "sandbox-b.example"
protocol = "https"
cli_executable = "{cli_b}"
runtime_attestation_file = "{attestation_b}"
runtime_attestation_public_key_file = "{public_key_b}"
image = "registry.example/fleet-runner@sha256:{"2" * 64}"
runtime = "kata"
api_key_env = "{key_b}"
'''.replace("egresS_pool", "egress_pool"),
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    configured = configured_executor_pool()

    assert [item.identity.executor_id for item in configured.executors] == [
        "local",
        "kata-a",
        "kata-b",
    ]
    assert isinstance(configured.executors[0].backend, BubblewrapProcessIsolation)
    assert isinstance(configured.executors[1].backend, OpenSandboxProcessIsolation)
    assert configured.executors[1].backend.settings.api_key == "secret-a"
    assert configured.executors[2].backend.settings.domain == "sandbox-b.example"


def test_executor_inventory_rejects_two_ids_for_same_local_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "local-a"
generation = 1
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/usr/bin/bwrap"
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"

[[executors]]
id = "local-b"
generation = 1
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/usr/bin/bwrap"
priority = 2
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(FleetPlanInvalid, match="only one local Bubblewrap executor"):
        configured_executor_pool()


def test_executor_inventory_requires_api_key_by_indirection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        f"""version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "remote"
generation = 1
execution = {{ environment = {{ backend = "opensandbox" }} }}
priority = 1
cpu_millis = 4000
memory_mib = 8192
domain = "sandbox.example"
image = "registry.example/fleet-runner@sha256:{"3" * 64}"
runtime = "kata"
api_key_env = "OH_GOON_MISSING_KEY"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(FleetPlanInvalid, match="OH_GOON_MISSING_KEY"):
        configured_executor_pool()


def test_bubblewrap_inventory_does_not_discover_backend_authority_from_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OH_GOON_EGRESS_POOL", "10.250.0.0/24")
    monkeypatch.setenv("OH_GOON_CGROUP_ROOT", "/ambient/cgroup")
    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "local"
generation = 1
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/declared/bin/bwrap"
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/declared/cgroup"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    configured = configured_executor_pool()
    backend = configured.executors[0].backend

    assert isinstance(backend, BubblewrapProcessIsolation)
    assert backend.executable == "/declared/bin/bwrap"
    assert backend.egress_pool is None
    assert backend.ip_executable is None
    assert backend.nft_executable is None
    assert backend.cgroup_root == Path("/declared/cgroup")


def test_bubblewrap_restricted_egress_inventory_is_all_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "local"
generation = 1
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/usr/bin/bwrap"
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
egress_pool = "10.253.0.0/24"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(
        FleetPlanInvalid, match="ip_executable, nft_executable, and egress_pool together"
    ):
        configured_executor_pool()


def test_strict_executor_inventory_has_no_single_executor_environment_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    monkeypatch.setenv("OH_GOON_EXECUTOR_ID", "legacy")
    monkeypatch.setenv("OH_GOON_EXECUTOR_CPU_MILLIS", "8000")
    monkeypatch.setenv("OH_GOON_EXECUTOR_MEMORY_MIB", "8192")

    assert configured_executor_pool() is None


def test_executor_inventory_path_must_be_exact_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "~/executors.toml")

    with pytest.raises(FleetPlanInvalid, match="must be an absolute path"):
        configured_executor_pool()


def test_executor_inventory_requires_explicit_remote_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "OH_GOON_TEST_REMOTE_KEY"
    monkeypatch.setenv(key, "secret")
    cli, attestation, public_key = _remote_assets(tmp_path, "sandbox.example")
    path = _write(
        tmp_path,
        f'''version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "remote"
generation = 1
execution = {{ environment = {{ backend = "opensandbox" }} }}
priority = 1
cpu_millis = 4000
memory_mib = 8192
domain = "sandbox.example"
cli_executable = "{cli}"
runtime_attestation_file = "{attestation}"
runtime_attestation_public_key_file = "{public_key}"
image = "registry.example/fleet-runner@sha256:{"4" * 64}"
runtime = "kata"
api_key_env = "{key}"
''',
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(FleetPlanInvalid, match="requires non-empty protocol"):
        configured_executor_pool()


def test_executor_inventory_rejects_unknown_fields_instead_of_ignoring_typos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]
unexpected_policy = "ignored-before"

[[executors]]
id = "local"
generation = 1
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/usr/bin/bwrap"
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(FleetPlanInvalid, match="unknown top level field.*unexpected_policy"):
        configured_executor_pool()

    path.write_text(
        path.read_text(encoding="utf-8")
        .replace('unexpected_policy = "ignored-before"\n', "")
        .replace(
            'cgroup_root = "/sys/fs/cgroup/oh-goon"',
            'cgroup_root = "/sys/fs/cgroup/oh-goon"\negres_pool = "10.253.0.0/24"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="unknown Bubblewrap executor.*egres_pool"):
        configured_executor_pool()


def test_application_pool_captures_missing_inventory_until_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.runtime.executor_pool import FleetExecutorPool

    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    captured = configured_executor_pool()
    assert captured is None
    pool = FleetExecutorPool(LocalDatabase(tmp_path / "runtime.db"), captured)

    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "local"
generation = 1
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/usr/bin/bwrap"
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(FleetPlanInvalid, match="restart"):
        pool.admit_strict_with(
            fleet_run_id="run-after-start",
            plan=_minimal_plan(),
            targets=(RepositoryIdentity(path=tmp_path / "repo"),),
            commit=lambda _connection, _placement: None,
        )


def _minimal_plan():
    from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
    from oh_goon.core.models.fleet_plan_execution_policy import (
        CicdExecutionPolicy,
    )
    from oh_goon.core.models.fleet_plan_isolation import (
        CicdResourcePolicy,
    )
    from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities

    return ResolvedFleetPlan.create(
        name="executor-config-capture",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=1024, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="noop",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version = 1\nexecutors = []\n", "version = 8"),
        (
            "version = 8\nexecutors = []\n",
            "placement_wait_seconds",
        ),
        (
            "version = 8\nstate_dir = '/tmp/state'\nplacement_wait_seconds = 0\nruntime_inspector = '/usr/bin/ldd'\ntrusted_host_roots = ['/usr/bin']\ntool_search_paths = ['/usr/bin']\nexecutors = []\n",
            "unknown top level field.*state_dir",
        ),
        (
            "version = 8\nplacement_wait_seconds = 3601\nplacement_queue_limit = 64\nruntime_inspector = '/usr/bin/ldd'\ntrusted_host_roots = ['/usr/bin']\ntool_search_paths = ['/usr/bin']\nexecutors = []\n",
            "placement_wait_seconds <= 3600",
        ),
        (
            "version = 8\nplacement_wait_seconds = 0\nplacement_queue_limit = 0\nruntime_inspector = '/usr/bin/ldd'\ntrusted_host_roots = ['/usr/bin']\ntool_search_paths = ['/usr/bin']\nexecutors = []\n",
            "placement_queue_limit >= 1",
        ),
        (
            "version = 8\nplacement_wait_seconds = 0\nplacement_queue_limit = 10001\nruntime_inspector = '/usr/bin/ldd'\ntrusted_host_roots = ['/usr/bin']\ntool_search_paths = ['/usr/bin']\nexecutors = []\n",
            "placement_queue_limit <= 10000",
        ),
    ],
)
def test_executor_inventory_rejects_obsolete_or_incomplete_v7_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    message: str,
) -> None:
    path = _write(tmp_path, body)
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(FleetPlanInvalid, match=message):
        configured_executor_pool()


def test_executor_inventory_requires_monotonic_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "local"
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/usr/bin/bwrap"
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(FleetPlanInvalid, match="generation"):
        configured_executor_pool()


def test_local_bubblewrap_control_state_uses_application_owned_private_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]

[[executors]]
id = "local"
generation = 1
execution = { environment = { backend = "bubblewrap" } }
bwrap_executable = "/usr/bin/bwrap"
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
""",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))
    control = tmp_path / "state" / "executor-control"

    configured = configured_executor_pool(control_state_root=control)
    backend = configured.executors[0].backend

    assert isinstance(backend, BubblewrapProcessIsolation)
    assert backend.egress_lock == control / "egress.lock"
    assert backend.egress_state_dir == control / "egress"
    assert backend.cgroup_lock == control / "cgroups.lock"
    assert backend.cgroup_state_dir == control / "cgroups"
    assert not any(
        str(path).startswith("/run/")
        for path in (
            backend.egress_lock,
            backend.egress_state_dir,
            backend.cgroup_lock,
            backend.cgroup_state_dir,
        )
    )


def test_executor_inventory_stale_schema_is_rejected_without_upgrade_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "version = 6\nexecutors = []\n")
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(
        FleetPlanInvalid,
        match=r"schema 6 is unsupported; current schema is 8.*required version = 8",
    ):
        configured_executor_pool()


def test_executor_inventory_future_schema_fails_closed_with_directional_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "version = 9\nexecutors = []\n")
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))

    with pytest.raises(
        FleetPlanInvalid, match=r"schema 9 is newer than this build supports.*version = 8"
    ):
        configured_executor_pool()


def test_executor_policy_bounds_are_validated_before_host_path_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "executors.toml"
    path.write_text(
        "version = 8\n"
        "placement_wait_seconds = 3601\n"
        "placement_queue_limit = 64\n"
        "runtime_inspector = '/definitely/not/present/ldd'\n"
        "trusted_host_roots = ['/definitely/not/present']\n"
        "tool_search_paths = ['/definitely/not/present']\n"
        "executors = []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(path))
    with pytest.raises(FleetPlanInvalid, match="placement_wait_seconds <= 3600"):
        configured_executor_pool()


@pytest.mark.parametrize(
    "bad_execution",
    [
        'kind = "bubblewrap"',
        'execution = { environment = { backend = "auto" } }',
        'execution = { environment = { backend = "native" } }',
        'execution = { environment = { backend = ["bubblewrap", "opensandbox"] } }',
        'execution = { environment = { backend = "bubblewrap", fallback = "opensandbox" } }',
        'execution = { backend = "bubblewrap" }',
        "",
    ],
)
def test_executor_v8_rejects_implicit_or_duplicate_backend_authority(
    tmp_path: Path,
    bad_execution: str,
) -> None:
    from oh_goon.runtime._executor_configuration import load_executor_pool

    path = _write(
        tmp_path,
        f"""version = 8
placement_wait_seconds = 0
placement_queue_limit = 1
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]
[[executors]]
id = "x"
generation = 1
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
bwrap_executable = "/usr/bin/bwrap"
{bad_execution}
""",
    )
    with pytest.raises(FleetPlanInvalid):
        load_executor_pool(path)


def test_configured_executor_environment_projects_selected_backend(tmp_path: Path) -> None:
    from oh_goon.runtime._executor_configuration import load_executor_pool

    path = _write(
        tmp_path,
        """version = 8
placement_wait_seconds = 0
placement_queue_limit = 1
runtime_inspector = "/usr/bin/ldd"
trusted_host_roots = ["/usr/bin"]
tool_search_paths = ["/usr/bin"]
[[executors]]
id = "x"
generation = 1
priority = 1
cpu_millis = 4000
memory_mib = 8192
cgroup_root = "/sys/fs/cgroup/oh-goon"
bwrap_executable = "/usr/bin/bwrap"
execution = { environment = { backend = "bubblewrap" } }
""",
    )
    inventory = load_executor_pool(path)
    executor = inventory.executors[0]
    pinned = executor.resolve_environment(inventory.path_authority)
    assert pinned.backend == "bubblewrap"
    assert pinned.executor_generation == 1
    pinned.require_unchanged(executor.resolve_environment(inventory.path_authority))
