from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
SURFACE_PATH = ROOT / "docs/development/MAKE_TARGET_SURFACE.yml"
WORKFLOW_DOC = ROOT / "docs/development/CANONICAL_WORKFLOW.md"
SELF_PLAN = ROOT / "templates/plans/oh-goon/self-verification/plan.yml"
WORKFLOW = ROOT / "backend/src/oh_goon/workflow.py"

CANONICAL_TARGETS = ["init", "check", "verify", "certify", "prepare-release"]

LIVE_OPERATOR_DOCS = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "docs/LOCAL_VERIFICATION.md",
    ROOT / "docs/DEVELOPMENT.md",
    ROOT / "docs/development/CANONICAL_WORKFLOW.md",
    ROOT / "docs/development/APP_TEST_RUN.md",
    ROOT / "docs/development/APP_CERTIFICATION.md",
    ROOT / "docs/development/ACCEPTANCE.md",
    ROOT / "docs/development/BROWSER_QUALITY_MATRIX.md",
    ROOT / "docs/development/FRONTEND_STYLE_TUNING.md",
    ROOT / "docs/development/RELEASE_REPRODUCIBILITY.md",
)

SELF_GOONS = {
    "oh-goon/backend-p0",
    "oh-goon/tooling-tests",
    "oh-goon/architecture",
    "oh-goon/generated-contracts",
    "oh-goon/integration-tests",
    "oh-goon/repository-exam",
}


def _phony_targets() -> set[str]:
    declarations = re.findall(r"^\.PHONY:[ \t]*(.*)$", MAKEFILE, re.MULTILINE)
    assert declarations
    return {target for declaration in declarations for target in declaration.split()}




def _defined_make_targets() -> list[str]:
    return [
        match.group(1)
        for line in MAKEFILE.splitlines()
        if (match := re.match(r"^([A-Za-z0-9_.-]+):(?:\s|$)", line)) is not None
        and not match.group(1).startswith(".")
    ]

def _recipe_targets() -> set[str]:
    lines = MAKEFILE.splitlines()
    targets: set[str] = set()
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Za-z0-9_.-]+):(?:\s.*)?$", line)
        if match is None or match.group(1).startswith("."):
            continue
        cursor = index + 1
        has_recipe = False
        while cursor < len(lines):
            candidate = lines[cursor]
            if candidate.startswith("\t"):
                has_recipe = True
                cursor += 1
                continue
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                cursor += 1
                continue
            break
        if has_recipe:
            targets.add(match.group(1))
    return targets


def _documented_make_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        targets.extend(re.findall(r"`make\s+([A-Za-z0-9_.-]+)(?:\s[^`]*)?`", line))
        shell = re.match(r"^\s*(?:\$\s*)?make\s+([A-Za-z0-9_.-]+)(?:\s|$)", line)
        if shell is not None:
            targets.append(shell.group(1))
    return targets

def test_every_make_target_has_exactly_one_frozen_surface_classification() -> None:
    data = yaml.safe_load(SURFACE_PATH.read_text(encoding="utf-8"))
    canonical = data["canonical_human_workflow"]["make_targets"]
    assert canonical == CANONICAL_TARGETS
    classes = data["noncanonical_make_target_classes"]
    assert set(classes) == {"owner-local-validation", "diagnostic", "internal-generated", "deprecated"}
    occurrences: dict[str, int] = {}
    for target in canonical:
        occurrences[target] = occurrences.get(target, 0) + 1
    for members in classes.values():
        for target in members:
            occurrences[target] = occurrences.get(target, 0) + 1
    assert set(occurrences) == _phony_targets()
    assert all(count == 1 for count in occurrences.values())


def test_focused_schedule_regression_has_exact_owner_local_validation_classification() -> None:
    """Every new Make owner must join the frozen surface before native execution."""
    target = "frontend-schedule-plan-input-regression"
    data = yaml.safe_load(SURFACE_PATH.read_text(encoding="utf-8"))
    classes = data["noncanonical_make_target_classes"]
    assert target in _phony_targets()
    assert target in classes["owner-local-validation"]
    assert sum(target in members for members in classes.values()) == 1
    assert target not in data["canonical_human_workflow"]["make_targets"]
    assert (
        "frontend-schedule-plan-input-regression:\n"
        "\tcd frontend && npm run test -- "
        "src/features/schedules/components/ScheduleCreatePanel.test.tsx "
        "src/features/schedules/components/schedulePlanInputDraft.test.ts"
    ) in MAKEFILE


def test_candidate_convergence_preflight_uses_source_authorities_without_archive_history() -> None:
    data = yaml.safe_load(SURFACE_PATH.read_text(encoding="utf-8"))
    internal = set(data["noncanonical_make_target_classes"]["internal-generated"])
    required = {
        "candidate-convergence-preflight",
        "native-qualification-boundary-check",
        "phase-evidence-convergence-check",
    }
    assert required <= internal
    body = MAKEFILE.split("candidate-convergence-preflight:", 1)[1].split("\n\n", 1)[0]
    assert "$(MAKE) native-qualification-boundary-check" in body
    assert "phase-evidence-convergence-check" not in body
    assert "checkpoint_is_current_state_not_embedded_phase_history" not in body
    assert "oh-goon-release-version check" in body
    assert "oh-goon-check-generated" in body
    assert "oh-goon-architecture" in body


def test_canonical_surface_has_one_release_plan_and_no_wrapper_aliases() -> None:
    data = yaml.safe_load(SURFACE_PATH.read_text(encoding="utf-8"))
    canonical = data["canonical_human_workflow"]
    assert canonical["ordered_frontdoors"] == [
        "make init", "make check", "make verify", "make certify"
    ]
    assert canonical["release_plan"] == "templates/plans/oh-goon/prepare-release/plan.yml"
    assert canonical["release_frontdoor"] == "make prepare-release"
    forbidden = set(data["forbidden_wrapper_aliases"])
    assert forbidden == {"quick", "dev-first", "certify-fast"}
    assert not (_phony_targets() & forbidden)
    doc = WORKFLOW_DOC.read_text(encoding="utf-8")
    assert "not a second release orchestrator" in doc


def test_canonical_self_verification_is_a_normal_bounded_plan() -> None:
    plan = yaml.safe_load(SELF_PLAN.read_text(encoding="utf-8"))
    goons = [item if isinstance(item, str) else item["import"] for item in plan["goons"]]
    assert set(goons) == SELF_GOONS
    for goon in SELF_GOONS:
        name = goon.removeprefix("oh-goon/")
        data = yaml.safe_load((ROOT / "templates/goons/oh-goon" / name / "goon.yml").read_text(encoding="utf-8"))
        assert len(data["steps"]) == 1
        # Normal run Steps intentionally do not author a second timeout policy.
        # Fleet-plan resolution applies the backend-owned bounded default before execution.
        assert "timeout_seconds" not in data["steps"][0]
    model = (ROOT / "backend/src/oh_goon/core/models/fleet_plan.py").read_text(encoding="utf-8")
    assert "DEFAULT_COMMAND_TIMEOUT_SECONDS = 3600.0" in model
    assert "Field(default=DEFAULT_COMMAND_TIMEOUT_SECONDS" in model
    source = WORKFLOW.read_text(encoding="utf-8")
    assert '"execution_authority": "FleetPlanService.run"' in source
    assert '"pipeline": ["resolve", "lock", "admit", "execute", "evidence"]' in source


def test_self_verification_reports_complete_or_incomplete_without_turning_timeout_into_test_failure() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "OH_GOON_SELF_VERIFICATION_STATUS=COMPLETE" in source
    assert "OH_GOON_SELF_VERIFICATION_STATUS=INCOMPLETE" in source
    assert "missing proof is not a test failure" in source


def test_every_shipped_goon_make_delegation_resolves_to_real_make_target() -> None:
    targets = set(re.findall(r"(?m)^([A-Za-z0-9_.-]+):(?:\s|$)", MAKEFILE))
    checked: list[tuple[str, str]] = []
    for path in sorted((ROOT / "templates/goons").rglob("goon.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in data.get("steps", []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str) or not run.startswith("make "):
                continue
            target = run.removeprefix("make ").split()[0]
            checked.append((str(path.relative_to(ROOT)), target))
            assert target in targets, (
                f"{path.relative_to(ROOT)} delegates to missing Make target {target!r}"
            )
            assert target in _phony_targets(), (
                f"{path.relative_to(ROOT)} delegates to non-.PHONY Make target {target!r}"
            )
    assert checked, "expected shipped Goons to exercise Make delegations"


def test_phony_surface_has_one_real_rule_per_target() -> None:
    defined = _defined_make_targets()
    assert len(defined) == len(set(defined)), (
        "named Make targets must have one rule owner"
    )
    assert set(defined) == _phony_targets(), (
        "named action targets and .PHONY surface must be bidirectionally complete"
    )


def test_every_explicit_recipe_target_is_phony() -> None:
    missing = sorted(_recipe_targets() - _phony_targets())
    assert not missing, (
        f"recipe-bearing Make targets must be .PHONY action targets: {missing}"
    )


def test_live_operator_documentation_only_names_real_phony_make_targets() -> None:
    phony = _phony_targets()
    missing: list[str] = []
    checked = 0
    for path in LIVE_OPERATOR_DOCS:
        for target in _documented_make_targets(path):
            checked += 1
            if target not in phony:
                missing.append(f"{path.relative_to(ROOT)}: make {target}")
    assert checked >= 100
    assert not missing, (
        "live operator documentation names stale/non-.PHONY Make targets: "
        + ", ".join(missing)
    )


def test_storage_maintenance_make_frontdoors_use_repository_local_cli() -> None:
    assert (
        "storage-status:\n"
        "\t@backend/.venv/bin/oh-goon storage status --measure"
    ) in MAKEFILE
    assert (
        "storage-prune:\n"
        "\t@backend/.venv/bin/oh-goon storage prune --yes"
    ) in MAKEFILE


def test_storage_maintenance_frontdoors_use_repository_local_cli() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    status = makefile.split("storage-status:", 1)[1].split("\n\n", 1)[0]
    prune = makefile.split("storage-prune:", 1)[1].split("\n\n", 1)[0]

    assert "backend/.venv/bin/oh-goon storage status --measure" in status
    assert "backend/.venv/bin/oh-goon storage prune --yes" in prune
    assert "backend-dependencies-check" not in status + prune
    assert "\n\toh-goon storage" not in status + prune


def test_recovery_make_frontdoors_launch_their_owner_without_backend_probe() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target, command in (
        ("recovery-status", "oh-goon-recovery-checkpoint verify"),
        ("recovery-checkpoint", "oh-goon-recovery-checkpoint checkpoint"),
        ("recovery-resume", "oh-goon-recovery-checkpoint resume"),
    ):
        body = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
        assert "backend-dependencies-check" not in body
        assert command in body
