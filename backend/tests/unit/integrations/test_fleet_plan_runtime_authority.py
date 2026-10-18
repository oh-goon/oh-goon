from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import ExecutionAuthorityDivergence
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
from oh_goon.integrations._fleet_plan_runtime_root import RuntimeRootBinding
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.executable_materialization_authority import (
    StepExecutableMaterializationReceipt,
)
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from oh_goon.integrations.fleet_plan_runtime_authority import (
    expected_runtime_authority_sources,
    observed_runtime_authority_payload,
    reconcile_runtime_authority,
    require_runtime_authority_observation,
)


def _attempt(tmp_path: Path, bindings: tuple[HostToolBinding, ...]):
    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for path in (
        paths.workspace,
        paths.home,
        paths.tmp,
        paths.cache,
        paths.config,
        paths.etc,
    ):
        path.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    return SimpleNamespace(
        paths=paths,
        host_tools=bindings,
        sandbox_bindings=(),
        runtime_root=RuntimeRootBinding(
            root=runtime_root,
            identity=snapshot_identity(runtime_root),
        ),
        executable_materialization=StepExecutableMaterializationReceipt(),
    )


def _host_tool(tmp_path: Path, name: str, data: bytes) -> HostToolBinding:
    root = tmp_path / "tools"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(data)
    path.chmod(0o755)
    identity = snapshot_identity(path)
    return HostToolBinding(
        name=name,
        source_path=path,
        snapshot_path=path,
        identity=identity,
        execution_sha256="sha256:" + hashlib.sha256(data).hexdigest(),
    )


def _request(attempt, argv: tuple[str, ...]) -> IsolatedProcessRequest:
    return IsolatedProcessRequest(
        attempt=attempt,
        argv=argv,
        cwd=attempt.paths.workspace,
        env={"PATH": "/untrusted/host/path", "CUSTOM": "value"},
    )


def test_runtime_reconciliation_records_workload_not_transport_launcher(tmp_path: Path) -> None:
    true = _host_tool(tmp_path, "true", b"\x7fELFstrict-true")
    attempt = _attempt(tmp_path, (true,))

    receipt = reconcile_runtime_authority(_request(attempt, ("true",)))

    assert receipt.executable_path == "/toolchain/bin/true"
    assert receipt.executable_identity == _raw_sha(true.execution_snapshot_path)
    assert receipt.provider_identity == "sha256:" + true.identity.sha256
    assert receipt.runtime_identity == "sha256:" + attempt.runtime_root.identity.sha256
    assert receipt.execution_materialization_identity == (
        attempt.executable_materialization.receipt_identity
    )
    assert receipt.interpreter_path is None
    assert receipt.interpreter_identity is None


def test_runtime_expectation_verifies_one_standalone_host_binding_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations import fleet_plan_runtime_authority

    true = _host_tool(tmp_path, "true", b"\x7fELFstrict-true")
    attempt = _attempt(tmp_path, (true,))
    original = fleet_plan_runtime_authority.verify_snapshot
    verified: list[Path] = []

    def counted(path: Path, expected, *, label: str, cancel_requested=None) -> None:
        verified.append(path)
        original(path, expected, label=label, cancel_requested=cancel_requested)

    monkeypatch.setattr(fleet_plan_runtime_authority, "verify_snapshot", counted)

    sources = expected_runtime_authority_sources(_request(attempt, ("true",)))

    assert verified == [true.snapshot_path]
    assert sources.economics is not None
    assert sources.economics.reused_units == 1


def test_runtime_reconciliation_records_admitted_shebang_interpreter(tmp_path: Path) -> None:
    node = _host_tool(tmp_path, "node", b"\x7fELFnode")
    npm = _host_tool(tmp_path, "npm", b"#!/toolchain/bin/node\nrequire('npm')\n")
    attempt = _attempt(tmp_path, (node, npm))

    receipt = reconcile_runtime_authority(_request(attempt, ("npm", "run", "test")))

    assert receipt.executable_path == "/toolchain/bin/npm"
    assert receipt.interpreter_path == "/toolchain/bin/node"
    assert receipt.interpreter_identity == _raw_sha(node.execution_snapshot_path)


def test_runtime_reconciliation_refuses_unadmitted_path_resolution(tmp_path: Path) -> None:
    true = _host_tool(tmp_path, "true", b"\x7fELFstrict-true")
    attempt = _attempt(tmp_path, (true,))

    with pytest.raises(ExecutionAuthorityDivergence) as caught:
        reconcile_runtime_authority(_request(attempt, ("python3", "-V")))

    assert caught.value.code == "EXECUTION_AUTHORITY_DIVERGENCE"
    assert "admitted sandbox PATH" in str(caught.value)


def test_runtime_reconciliation_refuses_unadmitted_shebang_interpreter(tmp_path: Path) -> None:
    npm = _host_tool(tmp_path, "npm", b"#!/toolchain/bin/python3\nprint('bad')\n")
    attempt = _attempt(tmp_path, (npm,))

    with pytest.raises(ExecutionAuthorityDivergence, match="undeclared interpreter"):
        reconcile_runtime_authority(_request(attempt, ("npm",)))


def test_remote_observation_must_match_exact_runtime_workload(tmp_path: Path) -> None:
    true = _host_tool(tmp_path, "true", b"\x7fELFstrict-true")
    attempt = _attempt(tmp_path, (true,))
    expected = reconcile_runtime_authority(_request(attempt, ("true",)))
    observed = observed_runtime_authority_payload(expected)
    require_runtime_authority_observation(expected, observed)

    observed["executable_identity"] = "sha256:" + "0" * 64
    with pytest.raises(ExecutionAuthorityDivergence, match="executable_identity"):
        require_runtime_authority_observation(expected, observed)


def _raw_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
