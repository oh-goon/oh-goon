from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture._structural_authority_workflow import (
    check_make_single_path,
)
from oh_goon.repository_tooling.architecture.structural_authority_contract import (
    check_structural_authority_contract,
)

ROOT = Path(__file__).resolve().parents[2]


def _minimal_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "backend" / "src" / "oh_goon").mkdir(parents=True)
    (root / "frontend" / "src" / "domain" / "api").mkdir(parents=True)
    (root / "frontend" / "src" / "app" / "api").mkdir(parents=True)
    (root / "frontend" / "src" / "app").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "src" / "domain" / "concurrency").mkdir(
        parents=True, exist_ok=True
    )
    (root / "scripts" / "init_doctor.sh").write_text(
        'UV_CACHE_DIR=${UV_CACHE_DIR:-}\nconv=fsync\necho "OH-GOON INIT DOCTOR"\necho "diagnostic only"\necho "Native uv/npm remain authoritative"\necho "No cache relocation"\n',
        encoding="utf-8",
    )
    (root / "scripts" / "development_test.sh").write_text(
        'run_check "Backend unit" test-python\n'
        'run_check "Tooling" test-tooling\n'
        'run_check "Integration" test-integration\n'
        'run_check "Frontend typecheck" frontend-typecheck\n'
        'run_check "Frontend unit" test-frontend\n',
        encoding="utf-8",
    )
    (root / "Makefile").write_text(
        "DEVELOPMENT_TEST_RUNNER = bash scripts/development_test.sh\n"
        "DEVELOPMENT_RESOURCE_RUNNER = bash scripts/development_test_resources.sh\n"
        'CERTIFICATION_RUNNER = env UV_CACHE_DIR="$(CERTIFICATION_UV_CACHE)" uv run --isolated --frozen --offline --no-dev --project backend oh-goon-workflow\n'
        "RUN_LOG_DIR ?= /tmp/oh-goon-test-logs\n"
        'DEVELOPMENT_TEST_FRONTDOOR = OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh test $(DEVELOPMENT_RESOURCE_RUNNER) $(DEVELOPMENT_TEST_RUNNER)\n'
        'CERTIFICATION_FRONTDOOR = OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh certify $(DEVELOPMENT_RESOURCE_RUNNER) $(CERTIFICATION_RUNNER)\n'
        "DEPENDENCY_POLICY = uv run --no-project python backend/src/oh_goon/repository_tooling/fixture_dependency_policy.py\n"
        "INIT_DOCTOR = sh scripts/init_doctor.sh\n\n"
        "init:\n\t@uv sync --project backend --locked --group dev --group quality --group build\n\t@cd frontend && npm ci --prefer-offline --ignore-scripts --no-audit --no-fund\n\n"
        "init-doctor:\n\t@$(INIT_DOCTOR)\n\n"
        "test:\n\t@$(DEVELOPMENT_TEST_FRONTDOOR)\n\n"
        'certify:\n\t@test -x "$(CERTIFICATION_RUNNER)" || { printf "Run: make init\\n" >&2; exit 2; }\n\t@$(CERTIFICATION_FRONTDOOR)\n\n'
        'certification-explain:\n\t@test -n "$(OH_GOON_RUN_ID)"\n\t@$(CERTIFICATION_RUNNER) explain "$(OH_GOON_RUN_ID)"\n\n'
        "prepare-release:\n\t@$(CERTIFICATION_RUNNER) release\n",
        encoding="utf-8",
    )
    (root / "backend" / "pyproject.toml").write_text(
        '[project]\nname = "oh-goon"\nversion = "0.0.0"\n'
        "[project.scripts]\n"
        'oh-goon-workflow = "oh_goon.services.certification_frontdoor:main"\n'
        'oh-goon-dependency-policy = "oh_goon.repository_tooling.fixture_dependency_policy:main"\n',
        encoding="utf-8",
    )
    dependency_policy = (
        root / "backend/src/oh_goon/repository_tooling/fixture_dependency_policy.py"
    )
    dependency_policy.parent.mkdir(parents=True, exist_ok=True)
    dependency_policy.write_text("def main():\n    return 0\n", encoding="utf-8")
    (root / "backend" / "src" / "oh_goon" / "workflow.py").write_text(
        "from oh_goon.core.source_layout_policy import assert_canonical_source_layout\n\n"
        "def _certification_environment():\n"
        '    lock_root: Path = Path("/state/locks")\n'
        '    return _backend_cli(["dev", "bootstrap-certification"], env={}), lock_root\n\n'
        "def _run_plan(lock_root: Path):\n"
        '    lock_path = lock_root / "app-certification.goon.lock"\n'
        '    lock_command = ["lock", "plan.yml", "--output", str(lock_path)]\n'
        '    run_command = ["plan", "run", "plan.yml", "--lock-file", str(lock_path)]\n'
        "    return lock_command, run_command\n",
        encoding="utf-8",
    )
    (root / "frontend" / "src" / "domain" / "api" / "ApiClient.ts").write_text(
        'interface ApiClient {\n  cancelFleetRun(runId: string): WriteOperationResult<"cancel">;\n}\n',
        encoding="utf-8",
    )
    (
        root / "frontend" / "src" / "domain" / "concurrency" / "MutationController.ts"
    ).write_text(
        'export type MutationScope = "fleet-run" | "locations" | "repositories" | "schedules" | "recovery" | "plans" | "league-lineups" | "dependency-selections";\n',
        encoding="utf-8",
    )
    (root / "frontend" / "src" / "domain" / "reads").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "src" / "domain" / "reads" / "readResources.ts").write_text(
        "const MUTATION_RESOURCES: Readonly<Record<MutationScope, readonly ReadResource[]>> = {\n"
        '  "fleet-run": ["fleet-projection", "run-history", "execution-overview", "schedule-state", "scout-observations"],\n'
        '  locations: ["locations", "league-map", "fleet-projection", "migration-projection"],\n'
        '  repositories: ["fleet-projection", "security-projection", "dependency-observations"],\n'
        '  schedules: ["schedule-state"],\n'
        '  recovery: ["recovery-state", "run-history"],\n'
        '  plans: ["shared-plans", "plan-templates"],\n'
        '  "league-lineups": ["league-lineups"],\n'
        '  "dependency-selections": ["dependency-selections"],\n'
        "};\n",
        encoding="utf-8",
    )
    (
        root
        / "frontend"
        / "src"
        / "domain"
        / "concurrency"
        / "PresentationLocationAuthority.ts"
    ).write_text(
        "class PresentationLocationAuthority {\n"
        '  private location = ""; private generation = 0;\n'
        "  observe(location: string) { if (location !== this.location) { this.location = location; this.generation += 1; return true; } return false; }\n"
        "  owns(owner: any) { return owner.location === this.location && owner.generation === this.generation; }\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "frontend" / "src" / "app" / "ApiClientProvider.tsx").write_text(
        "const feedbackAuthority = new PresentationLocationAuthority(readLocationHash());\n"
        "const mountAuthority = {}; // identities are mount-bound\n"
        "const [transport] = useState(() => ({ mutations, reads }));\n"
        "if (feedbackAuthority.observe(readLocationHash())) setMutationFeedback(null);\n"
        "const captureMutationFeedbackOwner = () => feedbackAuthority.capture();\n"
        "const reportMutationFeedback = (_scope, owner, result) => { if (feedbackAuthority.owns(owner)) setMutationFeedback(result); };\n"
        "transport.mutations.subscribeCommits((scope, causalityRevision) => transport.reads.invalidateMany(readScopesInvalidatedByMutation(scope), causalityRevision));\n"
        "export const useReportMutationFeedback = () => reportMutationFeedback;\n",
        encoding="utf-8",
    )
    (root / "frontend" / "src" / "app" / "FleetRunProvider.tsx").write_text(
        "interface Actions { cancelFleet(runId: string): Promise<void>; }\n"
        "const cancelFleet = useCallback(async (runId: string) => client.cancelFleetRun(runId), []);\n",
        encoding="utf-8",
    )
    return root


def _messages(root: Path) -> list[str]:
    return [
        violation.message for violation in check_structural_authority_contract(root)
    ]


def test_current_repository_satisfies_structural_authority_guard() -> None:
    assert check_structural_authority_contract(ROOT) == []


def test_make_workflow_assignment_whitespace_is_representation_invariant(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    makefile = root / "Makefile"
    baseline = _messages(root)
    makefile.write_text(
        makefile.read_text(encoding="utf-8")
        .replace(
            "DEVELOPMENT_TEST_RUNNER = bash scripts/development_test.sh",
            "DEVELOPMENT_TEST_RUNNER=bash scripts/development_test.sh",
        )
        .replace(
            "DEVELOPMENT_RESOURCE_RUNNER = bash scripts/development_test_resources.sh",
            "DEVELOPMENT_RESOURCE_RUNNER=bash scripts/development_test_resources.sh",
        )
        .replace(
            'DEVELOPMENT_TEST_FRONTDOOR = OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh test $(DEVELOPMENT_RESOURCE_RUNNER) $(DEVELOPMENT_TEST_RUNNER)',
            'DEVELOPMENT_TEST_FRONTDOOR=OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh test $(DEVELOPMENT_RESOURCE_RUNNER) $(DEVELOPMENT_TEST_RUNNER)',
        ),
        encoding="utf-8",
    )

    messages = _messages(root)
    assert not any(
        "DEVELOPMENT_TEST_RUNNER must have exactly one canonical" in message
        for message in messages
    )
    assert not any("one-line delegation" in message for message in messages)
    assert messages == baseline


@pytest.mark.parametrize(
    "resource_runners",
    ("", "$(DEVELOPMENT_RESOURCE_RUNNER) $(DEVELOPMENT_RESOURCE_RUNNER) "),
    ids=("bypass", "duplicate"),
)
@pytest.mark.parametrize(
    ("runner_variable", "diagnostic"),
    (
        (
            "DEVELOPMENT_TEST_RUNNER",
            "make test must remain a one-line delegation to the logged development test frontdoor",
        ),
        (
            "CERTIFICATION_RUNNER",
            "make certify must delegate exactly once to the logged certification frontdoor; APP ordering belongs to the Plan",
        ),
    ),
)
def test_logged_frontdoors_require_exactly_one_resource_wrapper(
    tmp_path: Path, resource_runners: str, runner_variable: str, diagnostic: str
) -> None:
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    makefile = root / "Makefile"
    baseline = (ROOT / "Makefile").read_text(encoding="utf-8")
    makefile.write_text(baseline, encoding="utf-8")
    (root / "backend/pyproject.toml").write_text(
        (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert check_make_single_path(root) == []

    mutated = baseline.replace(
        f"$(DEVELOPMENT_RESOURCE_RUNNER) $({runner_variable})",
        resource_runners + f"$({runner_variable})",
        1,
    )
    assert mutated != baseline
    makefile.write_text(mutated, encoding="utf-8")

    assert [violation.message for violation in check_make_single_path(root)] == [
        diagnostic
    ]


def test_make_workflow_assignment_operator_remains_semantic(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    makefile = root / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8").replace(
            "DEVELOPMENT_TEST_RUNNER = bash scripts/development_test.sh",
            "DEVELOPMENT_TEST_RUNNER := bash scripts/development_test.sh",
        ),
        encoding="utf-8",
    )

    assert any(
        "DEVELOPMENT_TEST_RUNNER must have exactly one canonical" in message
        for message in _messages(root)
    )


def test_workflow_entrypoint_ownership_is_toml_representation_invariant(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    pyproject = root / "backend" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'oh-goon-workflow = "oh_goon.services.certification_frontdoor:main"',
            "oh-goon-workflow='oh_goon.services.certification_frontdoor:main'",
        ),
        encoding="utf-8",
    )

    messages = _messages(root)
    assert (
        "backend pyproject.toml must own the oh-goon-workflow entrypoint"
        not in messages
    )
    assert "missing canonical pyproject entrypoint: oh-goon-workflow" not in messages


def test_workflow_bootstrap_command_is_python_representation_invariant(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    path = root / "backend" / "src" / "oh_goon" / "workflow.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '["dev", "bootstrap-certification"]',
            "('dev', 'bootstrap-certification')",
        ),
        encoding="utf-8",
    )

    assert not any("bootstrap capability" in message for message in _messages(root))


def test_workflow_bootstrap_guard_rejects_lower_level_identity_command(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    path = root / "backend" / "src" / "oh_goon" / "workflow.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '["dev", "bootstrap-certification"]',
            '["dev", "bootstrap-identity"]',
        ),
        encoding="utf-8",
    )

    messages = _messages(root)
    assert any("bootstrap capability" in message for message in messages)
    assert any("lower-level identity calls" in message for message in messages)


def test_release_version_owner_recipe_whitespace_is_representation_invariant(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    makefile = root / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8")
        + "\nrelease-version-set:\n"
        + '\t@test -n "$(OH_GOON_RELEASE_VERSION)"\n'
        + '\tbackend/.venv/bin/oh-goon-release-version  set "$(OH_GOON_RELEASE_VERSION)"\n',
        encoding="utf-8",
    )

    assert not any(
        "release-version-set must delegate exactly once" in message
        for message in _messages(root)
    )


def test_release_version_owner_recipe_wrong_argv_remains_rejected(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    makefile = root / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8")
        + "\nrelease-version-set:\n"
        + '\t@test -n "$(OH_GOON_RELEASE_VERSION)"\n'
        + '\tbackend/.venv/bin/oh-goon-release-version check "$(OH_GOON_RELEASE_VERSION)"\n',
        encoding="utf-8",
    )

    assert any(
        "release-version-set must delegate exactly once" in message
        for message in _messages(root)
    )


def test_guard_rejects_goon_order_leaking_into_make_test(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    path = root / "Makefile"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "test:\n\t@$(DEVELOPMENT_TEST_FRONTDOOR)",
            "test:\n\t@$(DEVELOPMENT_TEST_FRONTDOOR)\n\t@$(MAKE) test-python",
        ),
        encoding="utf-8",
    )
    assert any("one-line delegation" in message for message in _messages(root))


def test_guard_rejects_backend_domain_import_in_workflow_runner(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    path = root / "backend" / "src" / "oh_goon" / "workflow.py"
    path.write_text(
        "from oh_goon.services.plan_success_evidence import require_plan_success\n"
        + path.read_text(),
        encoding="utf-8",
    )
    assert any(
        "instead of importing product domain module" in message
        for message in _messages(root)
    )


def test_workflow_may_consume_dependency_neutral_strict_executor_vocabulary(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    path = root / "backend" / "src" / "oh_goon" / "workflow.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "from oh_goon.core.source_layout_policy import assert_canonical_source_layout",
            "from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV",
        ),
        encoding="utf-8",
    )

    assert not any(
        "strict_executor_authority" in message
        and "instead of importing product domain module" in message
        for message in _messages(root)
    )


def test_guard_rejects_persisted_pass_mirror(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    (root / "scripts" / "release_marker.py").write_text(
        'MARKER = ".app-passed"\n', encoding="utf-8"
    )
    assert any("mirror marker is forbidden" in message for message in _messages(root))


def test_guard_rejects_parallel_frontend_mutation_transport(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    feature = root / "frontend" / "src" / "features" / "danger" / "delete.ts"
    feature.parent.mkdir(parents=True)
    feature.write_text(
        'import { writeOperation } from "@/app/api/mutationTransport";\n',
        encoding="utf-8",
    )
    assert any(
        "must not bypass the mutation boundary" in message
        for message in _messages(root)
    )


def test_guard_rejects_operational_browser_persistence(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    feature = root / "frontend" / "src" / "features" / "runs" / "persist.ts"
    feature.parent.mkdir(parents=True)
    feature.write_text(
        'localStorage.setItem("activeRunId", runId);\n', encoding="utf-8"
    )
    assert any(
        "operational identity/lifecycle" in message for message in _messages(root)
    )


def test_guard_rejects_mutable_resource_without_server_fence(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    api = root / "frontend" / "src" / "domain" / "api" / "ApiClient.ts"
    api.write_text(
        "interface ApiClient {\n"
        '  cancelFleetRun(runId: string): WriteOperationResult<"cancel">;\n'
        '  removeLocation(path: string): WriteOperationResult<"remove">;\n'
        "}\n",
        encoding="utf-8",
    )
    assert any(
        "mutable-resource mutation removeLocation" in message
        for message in _messages(root)
    )


def test_guard_does_not_certify_mutation_resource_source_representation(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    controller = (
        root / "frontend" / "src" / "domain" / "concurrency" / "MutationController.ts"
    )
    resources = root / "frontend" / "src" / "domain" / "reads" / "readResources.ts"
    baseline = _messages(root)

    controller.write_text(
        controller.read_text(encoding="utf-8").replace('"fleet-run"', "'fleet-run'"),
        encoding="utf-8",
    )
    resources.write_text(
        resources.read_text(encoding="utf-8")
        .replace('"fleet-run": [', "'fleet-run': [")
        .replace('"fleet-projection"', "'fleet-projection'"),
        encoding="utf-8",
    )

    assert _messages(root) == baseline


def test_guard_does_not_certify_read_dependency_source_representation(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    resources = root / "frontend" / "src" / "domain" / "reads" / "readResources.ts"
    baseline = _messages(root)
    resources.write_text(
        resources.read_text(encoding="utf-8")
        + "const READ_RESOURCES = {\n"
        + '  "shared-plans": [\n'
        + '    "run-history",\n'
        + '    "shared-plans",\n'
        + "  ],\n"
        + '  "shared-plan-preview": [\n'
        + '    "fleet-projection",\n'
        + '    "shared-plans",\n'
        + '    "league-map",\n'
        + "  ],\n"
        + '  "league-lineup-inspection": ["league-map", "league-lineups"],\n'
        + "} as const;\n",
        encoding="utf-8",
    )
    assert _messages(root) == baseline


def test_guard_rejects_schedule_without_durable_multitab_fence(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    path = root / "backend" / "src" / "oh_goon" / "runtime" / "_database_schema.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'SCHEDULE_SCHEMA = "CREATE TABLE schedule_executions (schedule_id TEXT)"\n',
        encoding="utf-8",
    )
    assert any("multi-tab Schedule starts" in message for message in _messages(root))


def test_guard_rejects_direct_project_python_execution(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    makefile = root / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8")
        + "\nlegacy-tool:\n\tpython3 -S scripts/tool.py\n",
        encoding="utf-8",
    )
    assert any(
        "direct python/python3 execution" in message for message in _messages(root)
    )


def test_guard_rejects_direct_python_in_frontend_project_script(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    package = root / "frontend/package.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text(
        '{"scripts":{"bad":"uv run --frozen --project ../backend python tool.py"}}\n',
        encoding="utf-8",
    )
    assert any(
        "direct python/python3 execution" in message for message in _messages(root)
    )


def test_guard_rejects_system_wide_local_executor_control_state(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    isolation = (
        root
        / "backend"
        / "src"
        / "oh_goon"
        / "integrations"
        / "fleet_plan_bubblewrap_isolation.py"
    )
    isolation.parent.mkdir(parents=True, exist_ok=True)
    isolation.write_text(
        'control_state_root = Path("/run/oh-goon")\n'
        'egress_state_dir = control_root / "egress"\n'
        'cgroup_state_dir = control_root / "cgroups"\n',
        encoding="utf-8",
    )
    assert any("system-wide /run" in message for message in _messages(root))


def test_guard_allows_namespace_broker_destinations_without_host_control_state(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    isolation = (
        root / "backend/src/oh_goon/integrations/fleet_plan_bubblewrap_isolation.py"
    )
    isolation.parent.mkdir(parents=True, exist_ok=True)
    isolation.write_text(
        "from pathlib import Path, PurePosixPath\n"
        'broker_target = PurePosixPath("/run/oh-goon-broker")\n'
        "protected_targets = (broker_target,)\n"
        'arguments = ["--ro-bind", str(private_broker), str(broker_target)]\n'
        'control_root = Path.home() / ".local/share/oh-goon/executor-control"\n'
        'egress_lock = control_root / "egress.lock"\n'
    )
    assert not any("system-wide /run" in message for message in _messages(root))


@pytest.mark.parametrize(
    "source",
    [
        'selected = "/run/oh-goon/control"\nrenamed = selected\nprivate_root = Path(renamed)\n',
        'open("/run/lock/oh-goon/egress.lock", "w")\n',
        'backend(egress_state_dir="/run/oh-goon/egress")\n',
        'class Backend:\n    def __init__(self, *, cgroup_lock=PurePosixPath("/run/oh-goon/cgroups.lock")):\n        self.cgroup_lock = cgroup_lock\n',
    ],
)
def test_guard_rejects_host_control_state_aliases_and_effect_inputs(
    tmp_path: Path, source: str
) -> None:
    root = _minimal_repo(tmp_path)
    isolation = (
        root / "backend/src/oh_goon/integrations/fleet_plan_bubblewrap_isolation.py"
    )
    isolation.parent.mkdir(parents=True, exist_ok=True)
    isolation.write_text(source)
    assert any("system-wide /run" in message for message in _messages(root))


def test_guard_rejects_precommit_pythonpath_bootstrap(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    precommit = root / ".pre-commit-config.yaml"
    precommit.write_text(
        "repos:\n  - repo: local\n    hooks:\n      - id: source-layout\n"
        "        entry: bash -lc 'PYTHONPATH=backend/src python3 -m oh_goon.repository_tooling.source_layout check'\n",
        encoding="utf-8",
    )
    assert any(
        "pre-commit repository Python must use uv" in message
        for message in _messages(root)
    )


def test_guard_rejects_unregistered_repeated_repository_python_command(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    tooling = root / "backend/src/oh_goon/repository_tooling"
    tooling.mkdir(parents=True, exist_ok=True)
    (tooling / "ghost_command.py").write_text(
        'def main() -> int:\n    return 0\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
        encoding="utf-8",
    )
    assert any(
        "repeated repository Python command must be a named" in message
        for message in _messages(root)
    )


def test_guard_rejects_unregistered_repository_command_with_private_cli_name(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    tooling = root / "backend/src/oh_goon/repository_tooling"
    tooling.mkdir(parents=True, exist_ok=True)
    (tooling / "ghost_command.py").write_text(
        'def cli() -> int:\n    return 0\n\nif __name__ == "__main__":\n    raise SystemExit(cli())\n',
        encoding="utf-8",
    )
    assert any(
        "repeated repository Python command must be a named" in message
        for message in _messages(root)
    )


def test_guard_rejects_browser_release_package_lock_parser(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    release = root / "backend/src/oh_goon/repository_tooling/release"
    release.mkdir(parents=True, exist_ok=True)
    (release / "browser_acceptance_container.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "def locked_playwright_version(root: Path):\n"
        '    lock_path = root / "frontend/package-lock.json"\n'
        "    payload = json.loads(lock_path.read_text())\n"
        '    return payload.get("packages")\n',
        encoding="utf-8",
    )
    assert any(
        "browser acceptance must not parse package-lock semantics" in message
        for message in _messages(root)
    )


def test_guard_rejects_native_command_retry_semantics(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    native = root / "backend/src/oh_goon/repository_tooling/native_command.py"
    native.parent.mkdir(parents=True, exist_ok=True)
    native.write_text(
        (ROOT / "backend/src/oh_goon/repository_tooling/native_command.py").read_text(
            encoding="utf-8"
        )
        + "\nimport time\ntime.sleep(1)\n",
        encoding="utf-8",
    )
    assert any(
        "must not implement native-tool retry sleeps" in message
        for message in _messages(root)
    )


def test_guard_rejects_clearing_operator_uv_system_certs(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    native = root / "backend/src/oh_goon/repository_tooling/native_command.py"
    native.parent.mkdir(parents=True, exist_ok=True)
    native.write_text(
        (ROOT / "backend/src/oh_goon/repository_tooling/native_command.py").read_text(
            encoding="utf-8"
        )
        + '\nchild_env.pop("UV_SYSTEM_CERTS", None)\n',
        encoding="utf-8",
    )
    assert any(
        "inherit operator/native environment" in message for message in _messages(root)
    )


def test_guard_rejects_sourcing_bashrc_for_uv_configuration(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    path = root / "Makefile"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\ncert-env:\n\tsource ~/.bashrc && uv sync\n",
        encoding="utf-8",
    )
    assert any(
        "inherit operator uv/TLS environment" in message for message in _messages(root)
    )


def test_guard_rejects_init_doctor_becoming_pre_uv_admission(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    makefile = root / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    text = text.replace(
        "init:\n\t@uv sync --project backend --locked --group dev --group quality --group build\n\t@cd frontend && npm ci --prefer-offline --ignore-scripts --no-audit --no-fund",
        "init:\n\t@$(INIT_DOCTOR)\n\t@uv sync --project backend --locked --group dev --group quality --group build\n\t@cd frontend && npm ci --prefer-offline --ignore-scripts --no-audit --no-fund",
    )
    makefile.write_text(text, encoding="utf-8")
    assert any(
        "must not preflight host storage" in message for message in _messages(root)
    )


def test_guard_rejects_unbounded_or_raw_time_executor_placement(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    runtime = root / "backend/src/oh_goon/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "_executor_placement.py").write_text(
        "import time\ntime.sleep(0.05)\ntime.monotonic()\n", encoding="utf-8"
    )
    (runtime / "_executor_placement_queue.py").write_text(
        "def enter(self, fleet_run_id): pass\n", encoding="utf-8"
    )
    (runtime / "_executor_configuration.py").write_text(
        "EXECUTOR_CONFIG_VERSION = 6\n", encoding="utf-8"
    )

    messages = _messages(root)
    assert any(
        "shared monotonic clock/wait authority" in message for message in messages
    )
