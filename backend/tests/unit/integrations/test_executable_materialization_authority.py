from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.integrations.executable_materialization_authority import (
    LogicalStepExecutionAuthority,
    bind_step_executable_materialization,
    logical_step_execution_authority,
    verify_step_executable_materialization,
)
from oh_goon.integrations.package_script_execution_authority import derive_npm_run_authority


def _materialize_package(
    root: Path,
    *,
    provider: str = "typescript",
    version: str = "1.0.0",
    bin_name: str = "tsc",
    bin_target: str = "bin/tsc",
) -> tuple[Path, object]:
    frontend = root / "frontend"
    frontend.mkdir(parents=True)
    package_json = {
        "name": "fixture",
        "private": True,
        "scripts": {"typecheck": f"{bin_name} -b"},
        "devDependencies": {provider: "1"},
    }
    lock = {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "fixture", "devDependencies": {provider: "1"}},
            f"node_modules/{provider}": {
                "version": version,
                "integrity": f"sha512-{provider}",
                "bin": {bin_name: bin_target},
            },
        },
    }
    (frontend / "package.json").write_text(json.dumps(package_json), encoding="utf-8")
    (frontend / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")

    provider_root = frontend / "node_modules" / provider
    target = provider_root / bin_target
    target.parent.mkdir(parents=True)
    (provider_root / "package.json").write_text(
        json.dumps(
            {
                "name": provider,
                "version": version,
                "bin": {bin_name: bin_target},
            }
        ),
        encoding="utf-8",
    )
    target.write_text("console.log('bound')\n", encoding="utf-8")
    bin_dir = frontend / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    relative_target = os.path.relpath(target, bin_dir)
    (bin_dir / bin_name).symlink_to(relative_target)

    authority = derive_npm_run_authority(
        root,
        "typecheck",
        chain=("test", "npm run typecheck"),
        package_dir=frontend,
    ).package_executables[0]
    return frontend, authority


def _step() -> ResolvedCommandStep:
    return ResolvedCommandStep(
        timeout_seconds=30,
        step_id="materialized",
        argv=("make", "typecheck"),
    )


@pytest.mark.parametrize(
    "fragment",
    [
        'p=backend/.venv/bin/private-tool; "$p"',
        'command "$tool"',
        'env "$tool"',
    ],
)
def test_logical_explicit_shell_authority_rejects_unresolved_executable_before_materialization(
    tmp_path: Path,
    fragment: str,
) -> None:
    step = ResolvedCommandStep(
        timeout_seconds=30,
        step_id="dynamic-shell",
        argv=("sh", "-c", fragment),
    )

    with pytest.raises(FleetPlanInvalid, match="execution authority.*unproven") as exc:
        logical_step_execution_authority(tmp_path, step)

    assert "unresolved executable variable" in str(exc.value)


def test_package_authority_remembers_owning_package_directory(tmp_path: Path) -> None:
    _frontend, authority = _materialize_package(tmp_path)

    assert authority.package_directory == "frontend"


def test_materialized_package_bin_binds_locked_provider_and_verifies(tmp_path: Path) -> None:
    _frontend, authority = _materialize_package(tmp_path)
    receipt = bind_step_executable_materialization(
        tmp_path,
        _step(),
        LogicalStepExecutionAuthority(package_executables=(authority,)),
        allowed_host_tools=("make",),
    )

    package = receipt.package_executables[0]
    assert package.bin_entry == "frontend/node_modules/.bin/tsc"
    assert package.resolved_target == "frontend/node_modules/typescript/bin/tsc"
    assert package.receipt_identity.startswith("sha256:")
    assert receipt.economics is not None
    assert receipt.economics.reused_units == 0
    assert receipt.economics.recomputed_units == 2
    verification = verify_step_executable_materialization(tmp_path, receipt)
    assert verification.reused_units == 0
    assert verification.recomputed_units == 2
    assert verification.recompute_reasons == ("attempt-workspace-mutable-before-launch",)


def test_materialized_package_bin_rejects_retarget_to_another_locked_provider(
    tmp_path: Path,
) -> None:
    frontend, authority = _materialize_package(tmp_path)
    other = frontend / "node_modules" / "other"
    other.mkdir(parents=True)
    (other / "package.json").write_text(
        json.dumps({"name": "other", "version": "1", "bin": {"tsc": "cli.js"}}),
        encoding="utf-8",
    )
    (other / "cli.js").write_text("console.log('other')\n", encoding="utf-8")
    bin_entry = frontend / "node_modules" / ".bin" / "tsc"
    bin_entry.unlink()
    bin_entry.symlink_to(os.path.relpath(other / "cli.js", bin_entry.parent))

    with pytest.raises(FleetPlanInvalid, match="different provider target"):
        bind_step_executable_materialization(
            tmp_path,
            _step(),
            LogicalStepExecutionAuthority(package_executables=(authority,)),
            allowed_host_tools=("make",),
        )


def test_materialized_package_bin_rejects_escape_outside_node_modules(tmp_path: Path) -> None:
    frontend, authority = _materialize_package(tmp_path)
    outside = frontend / "outside.js"
    outside.write_text("console.log('escape')\n", encoding="utf-8")
    bin_entry = frontend / "node_modules" / ".bin" / "tsc"
    bin_entry.unlink()
    bin_entry.symlink_to(os.path.relpath(outside, bin_entry.parent))

    with pytest.raises(FleetPlanInvalid, match="escapes its admitted materialization root"):
        bind_step_executable_materialization(
            tmp_path,
            _step(),
            LogicalStepExecutionAuthority(package_executables=(authority,)),
            allowed_host_tools=("make",),
        )


def test_materialized_package_bin_rejects_regular_wrapper(tmp_path: Path) -> None:
    frontend, authority = _materialize_package(tmp_path)
    bin_entry = frontend / "node_modules" / ".bin" / "tsc"
    bin_entry.unlink()
    bin_entry.write_text("wrapper\n", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="must be an npm symlink"):
        bind_step_executable_materialization(
            tmp_path,
            _step(),
            LogicalStepExecutionAuthority(package_executables=(authority,)),
            allowed_host_tools=("make",),
        )


def test_package_provider_mutation_invalidates_prelaunch_receipt(tmp_path: Path) -> None:
    frontend, authority = _materialize_package(tmp_path)
    receipt = bind_step_executable_materialization(
        tmp_path,
        _step(),
        LogicalStepExecutionAuthority(package_executables=(authority,)),
        allowed_host_tools=("make",),
    )
    (frontend / "node_modules" / "typescript" / "extra.txt").write_text(
        "mutated\n", encoding="utf-8"
    )

    with pytest.raises(FleetPlanInvalid, match="changed after materialization binding"):
        verify_step_executable_materialization(tmp_path, receipt)


def test_unrelated_dependency_mutation_does_not_invalidate_bounded_provider_receipt(
    tmp_path: Path,
) -> None:
    frontend, authority = _materialize_package(tmp_path)
    unrelated = frontend / "node_modules" / "unrelated"
    unrelated.mkdir()
    (unrelated / "package.json").write_text(
        json.dumps({"name": "unrelated", "version": "1"}), encoding="utf-8"
    )
    receipt = bind_step_executable_materialization(
        tmp_path,
        _step(),
        LogicalStepExecutionAuthority(package_executables=(authority,)),
        allowed_host_tools=("make",),
    )
    (unrelated / "new.txt").write_text("irrelevant\n", encoding="utf-8")

    verify_step_executable_materialization(tmp_path, receipt)


def test_logical_execution_source_drift_is_rejected_before_materialization(tmp_path: Path) -> None:
    source = tmp_path / "Makefile"
    source.write_text("target:\n\ttrue\n", encoding="utf-8")
    admitted = "sha256:" + __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    source.write_text("target:\n\tfalse\n", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="no longer matches admitted identity"):
        bind_step_executable_materialization(
            tmp_path,
            _step(),
            LogicalStepExecutionAuthority(source_identities=(("Makefile", admitted),)),
            allowed_host_tools=("make",),
        )


def test_generated_shell_script_cannot_acquire_undeclared_child_tool(tmp_path: Path) -> None:
    script = tmp_path / "generated.sh"
    script.write_text("sed -n 1p input.txt\n", encoding="utf-8")
    (tmp_path / "input.txt").write_text("hello\n", encoding="utf-8")
    step = ResolvedCommandStep(
        timeout_seconds=30,
        step_id="generated",
        argv=("sh", "./generated.sh"),
    )

    with pytest.raises(FleetPlanInvalid, match="undeclared host executable authority: sed"):
        bind_step_executable_materialization(
            tmp_path,
            step,
            LogicalStepExecutionAuthority(),
            allowed_host_tools=("sh",),
        )


def test_generated_shell_script_bytes_are_revalidated_before_launch(tmp_path: Path) -> None:
    script = tmp_path / "generated.sh"
    script.write_text("sed -n 1p input.txt\n", encoding="utf-8")
    (tmp_path / "input.txt").write_text("hello\n", encoding="utf-8")
    step = ResolvedCommandStep(
        timeout_seconds=30,
        step_id="generated",
        argv=("sh", "./generated.sh"),
    )
    receipt = bind_step_executable_materialization(
        tmp_path,
        step,
        LogicalStepExecutionAuthority(),
        allowed_host_tools=("sh", "sed"),
    )
    script.write_text("sed -n 2p input.txt\n", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="Owned executable script changed"):
        verify_step_executable_materialization(tmp_path, receipt)


def test_materialization_refusal_occurs_before_process_backend_launch(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from oh_goon.integrations.fleet_plan_step_execution import _isolated_command_runner

    frontend, authority = _materialize_package(tmp_path)
    step = _step()
    receipt = bind_step_executable_materialization(
        tmp_path,
        step,
        LogicalStepExecutionAuthority(package_executables=(authority,)),
        allowed_host_tools=("make",),
    )
    target = frontend / "node_modules" / "typescript" / "bin" / "tsc"
    target.write_text("console.log('retargeted')\n", encoding="utf-8")

    class Backend:
        called = False

        def execute(self, *args, **kwargs):
            self.called = True
            raise AssertionError("process backend must not be called after authority refusal")

    backend = Backend()
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=tmp_path),
        executable_materialization=receipt,
    )
    runner = _isolated_command_runner(
        attempt, step, backend, termination_requested=None
    )

    with pytest.raises(FleetPlanInvalid, match="changed after materialization binding"):
        runner("make", "typecheck", cwd=tmp_path, env={})
    assert backend.called is False


def test_direct_workspace_executable_symlink_fails_closed_before_toctou_phase(
    tmp_path: Path,
) -> None:
    target = tmp_path / "generated-bin"
    target.write_bytes(b"\x7fELFfixture")
    link = tmp_path / "generated-link"
    link.symlink_to(target.name)
    step = ResolvedCommandStep(
        timeout_seconds=30,
        step_id="generated-bin",
        argv=("./generated-link",),
    )

    with pytest.raises(FleetPlanInvalid, match="direct workspace executable symlink"):
        bind_step_executable_materialization(
            tmp_path,
            step,
            LogicalStepExecutionAuthority(),
            allowed_host_tools=(),
        )
