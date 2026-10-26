from __future__ import annotations

import ast
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
from oh_goon.integrations.fleet_plan_compilation import (
    compile_plan_document,
    parse_fleet_yaml_document,
)

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
RUN_LOGGED = (ROOT / "scripts/run_logged.sh").read_text(encoding="utf-8")
DEVELOPMENT_TEST = (ROOT / "scripts/development_test.sh").read_text(encoding="utf-8")


def _target_body(name: str) -> str:
    marker = f"\n{name}:"
    start = MAKEFILE.index(marker) + 1
    rest = MAKEFILE[start:]
    next_target = rest.find("\n\n")
    return rest if next_target < 0 else rest[:next_target]


def _native_batch_commands(target: str) -> list[list[str]]:
    environment = os.environ.copy()
    for name in (
        "BACKEND_CHECK_TEST_ROOTS",
        "CHECK_PYTEST_MARKER",
        "TOOLING_TEST_MARKER",
        "TOOLING_COMPLETE_TEST_MARKER",
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
    ):
        environment.pop(name, None)
    result = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", target],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    commands = []
    for line in result.stdout.replace("\\\n", " ").splitlines():
        words = shlex.split(line)
        for index, word in enumerate(words):
            if word.endswith("/oh-goon-test-batches"):
                commands.append(words[index:])
                break
    return commands


def test_repository_understanding_diagnostic_batches_do_not_control_default_tooling_scheduling() -> (
    None
):
    tooling = _target_body("test-tooling")
    batched = _target_body("repository-understanding-tests-batched")
    assert "oh-goon-test-batches scripts/tests --granularity files" in tooling
    assert "--exclude " not in tooling
    native = _native_batch_commands("test-tooling")
    assert native and all(
        command[command.index("--marker") + 1]
        == "not certification and not process and not slow"
        for command in native
    )
    assert "--no-timeout-bisection" not in tooling
    assert "--granularity tests" in batched
    assert "--workers 1" in batched
    single = _target_body("repository-understanding-test-batch")
    assert "--workers 1" in single


def test_certification_preflight_is_local_and_has_no_dependency_reacquisition() -> None:
    certify = _target_body("certify")
    preflight = _target_body("certification-preflight")
    assert "certification-database-preflight" not in certify.splitlines()[0]
    assert "certification-database-preflight" in preflight.splitlines()[0]
    assert "acceptance-retained-supply" not in MAKEFILE
    assert "certification-dependencies" not in preflight
    assert "make init" not in preflight
    assert "CERTIFICATION PREFLIGHT PASSED" in preflight


def test_release_only_reacquisition_is_not_part_of_normal_certification_preflight() -> (
    None
):
    preflight = _target_body("certification-preflight")
    assert "acceptance-retained-supply" not in preflight
    assert "init" not in preflight
    assert "certification-setup" not in preflight


def test_lean_certification_consumes_native_init_and_has_no_repair_or_supply_frontdoor() -> (
    None
):
    path = ROOT / "templates/plans/oh-goon/app-certification/plan.yml"
    document = parse_fleet_yaml_document(
        path.read_text(encoding="utf-8"), source=str(path)
    )
    compiled = compile_plan_document(document, source=str(path))
    plan = document.payload
    assert compiled.execution is not None
    assert compiled.execution.purpose is ExecutionPurpose.VERIFICATION
    assert plan["goons"] == [
        "oh-goon/backend-p0",
        "oh-goon/architecture",
        "oh-goon/template-startability",
        "oh-goon/frontend-functional",
        "oh-goon/local-runtime",
    ]
    assert not (ROOT / "templates/goons/oh-goon/dependency-materialize").exists()
    for removed in (
        "certification-setup:",
        "certification-repair-backend:",
        "certification-workspace-setup:",
    ):
        assert f"\n{removed}" not in f"\n{MAKEFILE}"
    assert not (ROOT / "templates/plans/oh-goon/certification-backend-repair").exists()


def test_full_certification_delegates_database_preflight_to_the_leased_frontdoor() -> (
    None
):
    certify = _target_body("certify")
    assert "certification-database-preflight" not in certify.splitlines()[0]
    assert "goon-static-admission" not in certify.splitlines()[0]
    assert "$(CERTIFICATION_FRONTDOOR)" in certify
    assert "CERTIFICATION INITIALIZATION REQUIRED" in certify
    assert "Run: make init" in certify
    frontdoor = (
        ROOT / "backend/src/oh_goon/services/certification_frontdoor.py"
    ).read_text(encoding="utf-8")
    assert "with certification_state_lease(state):" in frontdoor
    assert "preflight_certification_database_under_lease(" in frontdoor


def test_logged_frontdoors_emit_banner_before_running_child() -> None:
    banner = RUN_LOGGED.index("command_banner.sh")
    child = RUN_LOGGED.index('"$@" >&"$command_log_fd" 2>&1 &')
    assert banner < child


def test_command_supervision_avoids_multithreaded_preexec_and_ru_cold_parallelism() -> (
    None
):
    command = (ROOT / "backend/src/oh_goon/engine/command.py").read_text(
        encoding="utf-8"
    )
    guard = (ROOT / "backend/src/oh_goon/runtime/exec_guard.py").read_text(
        encoding="utf-8"
    )
    command_tree = ast.parse(command)
    popen_calls = [
        node
        for node in ast.walk(command_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    ]
    assert popen_calls
    assert all(
        all(keyword.arg != "preexec_fn" for keyword in call.keywords)
        for call in popen_calls
    )
    assert "_EXEC_GUARD_PATH" in command
    assert '"-S", str(_EXEC_GUARD_PATH)' in command
    # Package-mode import is legitimate; the direct -S source-file branch must
    # use the colocated stdlib-only protocol with no package import.
    guard_tree = ast.parse(guard)
    conditional = next(
        node
        for node in guard_tree.body
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.ImportFrom)
            and child.module == "oh_goon.runtime"
            and any(alias.name == "exec_guard_protocol" for alias in child.names)
            for child in node.body
        )
    )
    # Python owns the branch decision; a type-only condition must never make
    # direct -S execution import the installed product package.
    condition = compile(ast.Expression(conditional.test), "exec-guard-import", "eval")
    for package, expected in ((None, False), ("", False), ("oh_goon.runtime", True)):
        assert (
            bool(
                eval(
                    condition,
                    {"__builtins__": {}},
                    {"TYPE_CHECKING": False, "__package__": package},
                )
            )
            is expected
        )
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "oh_goon.runtime"
        for node in conditional.body
    )
    direct_imports = {
        (alias.name, alias.asname or alias.name)
        for node in conditional.orelse
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    direct_dynamic_bindings = {
        (target.id, ast.unparse(node.value))
        for node in conditional.orelse
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ("exec_guard_protocol", "exec_guard_protocol") in direct_imports or (
        "exec_guard_protocol",
        "importlib.import_module('exec_guard_protocol')",
    ) in direct_dynamic_bindings
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("oh_goon")
        for branch in conditional.orelse
        for node in ast.walk(branch)
    )
    assert "os.setsid()" in guard


def test_batch_runner_splits_and_reports_without_monolithic_first_attempt(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_batch_demo.py"
    test_file.write_text(
        "".join(f"def test_{i}():\n    assert True\n\n" for i in range(5)),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "backend/src/oh_goon/repository_tooling/test_batches.py",
            str(test_file),
            "--granularity",
            "tests",
            "--batch-size",
            "2",
            "--workers",
            "2",
            "--batch-timeout",
            "30",
            "--marker",
            "",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout
    assert "members=5 | batches=3" in result.stdout
    assert "PYTEST BATCH 1/3 START" not in result.stdout
    assert "PYTEST BATCH HEARTBEAT" not in result.stdout
    assert "PYTEST BATCH RESULT: PASS" in result.stdout


def test_repository_understanding_process_cache_returns_defensive_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling import repository_understanding_material as material

    # This is a defensive-copy unit contract, not a Repository Understanding build
    # benchmark.  Inject tiny immutable cached facts so the fast lane proves copy
    # semantics without cold-building the complete repository snapshot in a fresh
    # pytest process.
    cached_exam = {"questions": [{"id": "Q01"}], "snapshot_sha256": "snapshot"}
    cached_manifest = {"question_count": 7, "manifest_sha256": "manifest"}
    monkeypatch.setattr(material, "_exam_snapshot_cached", lambda: cached_exam)
    monkeypatch.setattr(material, "_manifest_cached", lambda: cached_manifest)

    first = material.exam_snapshot()
    first["questions"][0]["id"] = "MUTATED"
    assert material.exam_snapshot()["questions"][0]["id"] == "Q01"

    first_manifest = material.manifest()
    first_manifest["question_count"] = -1
    assert material.manifest()["question_count"] == 7


def test_development_test_defers_native_frontend_operations_until_backend_feedback() -> (
    None
):
    backend_core = DEVELOPMENT_TEST.index(
        'if command -v "$HOST_PYTHON" >/dev/null 2>&1'
    )
    backend_late = DEVELOPMENT_TEST.index('run_check "Backend fast unit" test-python')
    backend_integration = DEVELOPMENT_TEST.index(
        'run_check "Backend integration" test-integration'
    )
    frontend_tests = DEVELOPMENT_TEST.index(
        'run_check "Frontend typecheck" frontend-typecheck'
    )
    assert "prepare_frontend" not in DEVELOPMENT_TEST
    assert "prepare_backend && backend_ready=1" not in DEVELOPMENT_TEST
    assert backend_core < backend_late < backend_integration < frontend_tests


def test_backend_fast_and_complete_lanes_use_semantic_execution_boundaries() -> None:
    fast = _target_body("test-python")
    complete = _target_body("test-python-complete")
    assert "--no-timeout-bisection" not in fast
    assert "--exclude " not in fast
    assert "not slow and not process and not scale and not certification" in fast
    assert "--exclude " not in complete
    assert "--environment-retries 1" in complete
    assert "--marker 'not process'" in complete
    assert "--marker 'process'" in complete


def test_tooling_fast_and_complete_lanes_keep_distinct_semantic_authority() -> None:
    fast = _target_body("test-tooling")
    complete = _target_body("test-tooling-complete")
    goon = yaml.safe_load(
        (ROOT / "templates/goons/oh-goon/tooling-tests/goon.yml").read_text(
            encoding="utf-8"
        )
    )
    native = _native_batch_commands("test-tooling")
    assert native and all(
        command[command.index("--marker") + 1]
        == "not certification and not process and not slow"
        for command in native
    )
    native_complete = _native_batch_commands("test-tooling-complete")
    assert native_complete and all(
        command[command.index("--marker") + 1] == "not certification"
        for command in native_complete
    )
    assert "--exclude " not in fast
    assert "--exclude " not in complete
    assert goon["execution"] == {"shell": "bash"}
    assert goon["steps"] == [{"run": "make test-tooling-complete"}]


def test_repository_understanding_tests_use_normal_import_cache_not_dynamic_execution() -> (
    None
):
    forbidden_calls = {"spec_from_file_location", "module_from_spec", "exec_module"}
    for path in (ROOT / "scripts/tests").glob("test_repository_understanding*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assert "from functools import lru_cache" not in source
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    target = (
                        decorator.func if isinstance(decorator, ast.Call) else decorator
                    )
                    name = (
                        target.id
                        if isinstance(target, ast.Name)
                        else target.attr
                        if isinstance(target, ast.Attribute)
                        else None
                    )
                    assert name not in {"lru_cache", "cache"}
            if isinstance(node, ast.Call):
                target = node.func
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                assert name not in forbidden_calls

    certification = (
        ROOT / "scripts/tests/test_repository_understanding_certification.py"
    ).read_text(encoding="utf-8")
    assert (
        "from oh_goon.repository_tooling import repository_understanding_exam"
        in certification
    )


def test_repository_understanding_material_is_the_only_ru_process_cache_owner() -> None:
    tooling = ROOT / "backend/src/oh_goon/repository_tooling"
    cached: dict[str, set[str]] = {}
    for path in tooling.glob("repository_understanding_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                if name in {"lru_cache", "cache"}:
                    names.add(node.name)
                    # Process-singleton material caches must be no-argument. Parameterized
                    # lru_cache here is easy to make stale and can grow invisibly.
                    assert not node.args.args and not node.args.kwonlyargs
        if names:
            cached[path.name] = names

    assert cached == {
        "repository_understanding_material.py": {
            "load_exam",
            "_benchmark_registry_cached",
            "_question_audit_cached",
            "_question_lifecycle_cached",
            "_qualification_blueprint_audit_cached",
            "_exam_snapshot_cached",
            "_manifest_cached",
        }
    }


def test_repository_understanding_cached_mutable_material_has_defensive_public_wrappers() -> (
    None
):
    material = (
        ROOT
        / "backend/src/oh_goon/repository_tooling/repository_understanding_material.py"
    ).read_text(encoding="utf-8")
    pairs = {
        "_benchmark_registry_cached": "benchmark_registry",
        "_question_audit_cached": "question_audit",
        "_question_lifecycle_cached": "question_lifecycle",
        "_qualification_blueprint_audit_cached": "qualification_blueprint_audit",
        "_exam_snapshot_cached": "exam_snapshot",
        "_manifest_cached": "manifest",
    }
    tree = ast.parse(material)
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    for cached_name, public_name in pairs.items():
        assert cached_name in functions and public_name in functions
        public_source = ast.unparse(functions[public_name])
        assert "copy.deepcopy" in public_source
        assert cached_name in public_source


def test_repository_understanding_cache_owner_never_imports_exam_or_test_orchestration() -> (
    None
):
    material = (
        ROOT
        / "backend/src/oh_goon/repository_tooling/repository_understanding_material.py"
    ).read_text(encoding="utf-8")
    assert "repository_understanding_exam" not in material
    assert "scripts.tests" not in material


def test_repository_understanding_process_cache_has_no_long_lived_runtime_reverse_references() -> (
    None
):
    package = ROOT / "backend/src/oh_goon"
    violations: list[str] = []
    for path in package.rglob("*.py"):
        if "repository_tooling" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "repository_understanding_material" in source:
            violations.append(path.as_posix())
    assert violations == []


def test_repository_understanding_private_cache_functions_have_no_production_bypasses() -> (
    None
):
    private_caches = {
        "_benchmark_registry_cached",
        "_question_audit_cached",
        "_question_lifecycle_cached",
        "_exam_snapshot_cached",
        "_manifest_cached",
    }
    violations: list[str] = []
    tooling = ROOT / "backend/src/oh_goon/repository_tooling"
    owner = tooling / "repository_understanding_material.py"
    for path in tooling.glob("repository_understanding_*.py"):
        if path == owner:
            continue
        source = path.read_text(encoding="utf-8")
        for name in private_caches:
            if name in source:
                violations.append(f"{path.as_posix()}:{name}")
    assert violations == []


def test_fast_semantic_batch_lanes_persist_exact_resume_receipts() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    fast = makefile[
        makefile.index("test-python:") : makefile.index("test-python-complete:")
    ]
    tooling = makefile[
        makefile.index("test-tooling:") : makefile.index("test-tooling-complete:")
    ]
    complete_backend = makefile[
        makefile.index("test-python-complete:") : makefile.index(
            "repository-understanding-test-batch:"
        )
    ]
    complete_tooling = makefile[
        makefile.index("test-tooling-complete:") : makefile.index("test-frontend:")
    ]

    assert fast.count("$(PYTEST_RESUME) --resume-key") == 1
    assert (
        tooling.count("$(PYTEST_RESUME) $(TOOLING_TEST_RESUME_CONTEXT) --resume-key")
        == 1
    )
    assert "--resume-key backend-fast-unit-files" in fast
    assert "--resume-key tooling-fast-files" in tooling
    assert "--exclude " not in fast
    assert "--exclude " not in tooling
    assert "PYTEST_RESUME ?= --resume" in MAKEFILE
    assert "node=$$(node --version" in MAKEFILE
    assert "npm=$$(npm --version" in MAKEFILE
    assert "uv=$$(uv --version" in MAKEFILE
    assert "git=$$(git --version" in MAKEFILE
    assert "--resume --resume-key" not in complete_backend
    assert "--resume --resume-key" not in complete_tooling


def test_runtime_liveness_and_tool_lookup_are_owned_not_textual() -> None:
    workspaces = (ROOT / "backend/src/oh_goon/runtime/workspaces.py").read_text(
        encoding="utf-8"
    )
    lease = (
        ROOT / "backend/src/oh_goon/runtime/execution/lease_environment.py"
    ).read_text(encoding="utf-8")
    process_birth = (ROOT / "backend/src/oh_goon/core/process_birth.py").read_text(
        encoding="utf-8"
    )
    process_tests = (ROOT / "scripts/tests/test_test_batches_process.py").read_text(
        encoding="utf-8"
    )

    assert '"PATH": os.environ.get("PATH", os.defpath)' in workspaces
    assert "observe_process_liveness(pid)" in lease
    assert 'state not in {"Z", "X", "x"}' in process_birth
    assert (
        "test_long_lived_batch_runner_cleans_completed_pytest_background_groups"
        in process_tests
    )
    assert (
        "test_repeated_batch_commands_leave_no_reader_threads_or_fd_growth"
        in process_tests
    )


def test_preexec_usage_is_confined_to_single_threaded_opensandbox_chroot_boundary() -> (
    None
):
    backend = ROOT / "backend/src/oh_goon"
    offenders: list[str] = []
    for path in backend.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not any(keyword.arg == "preexec_fn" for keyword in node.keywords):
                continue
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
    runner = (
        ROOT / "backend/src/oh_goon/repository_tooling/opensandbox_attempt_runner.py"
    ).read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" not in runner
    assert "os.chroot(root)" in runner
    assert "os.setsid()" in runner


def test_python_profile_uses_qualified_native_batches_and_retains_duration_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling.pytest_batch_cli import parse_batch_args

    (command,) = _native_batch_commands("test-python-profile")
    monkeypatch.setattr(sys, "argv", command)
    _parser, options = parse_batch_args()

    assert options.qualified is True
    assert options.pytest_config == Path("pyproject.toml")
    assert options.paths == [Path("tests/unit"), Path("tests/dependency_complete")]
    assert options.marker == ""
    assert options.pytest_arg == ["--durations=40", "--durations-min=0.05"]
    assert options.show_output is True
    assert not options.coverage_source


def test_integration_gates_keep_original_serial_selection_in_both_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.repository_tooling.pytest_batch_cli import parse_batch_args

    profiles = []
    for command in _native_batch_commands("test-integration"):
        monkeypatch.setattr(sys, "argv", command)
        _parser, options = parse_batch_args()
        profiles.append(options)

    assert len(profiles) == 2
    assert all(profile.qualified for profile in profiles)
    assert all(profile.show_output for profile in profiles)
    assert [profile.paths for profile in profiles] == [[Path("tests/integration")]] * 2
    assert [profile.pytest_config for profile in profiles] == [
        Path("pyproject.toml"),
        Path("backend/pyproject.toml"),
    ]
    assert [profile.marker for profile in profiles] == [
        "not certification and not slow"
    ] * 2
    assert [profile.workers for profile in profiles] == [1, 1]
    assert [profile.batch_size for profile in profiles] == [4, 4]
