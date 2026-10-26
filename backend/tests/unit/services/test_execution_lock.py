from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import operation_intent_key
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.execution_lock_persistence import (
    parse_execution_lock,
    render_execution_lock,
)
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import prepare_reviewed_execution_target


def _package(root: Path, key: str, body: str) -> Path:
    category, name = key.split("/")
    directory = root / category / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "goon.yml"
    path.write_text(body, encoding="utf-8")
    (directory / "README.md").write_text(f"# {key}\n", encoding="utf-8")
    return path


def _atomic(name: str, command: str = "true", requirement: str | None = None) -> str:
    requirement_yaml = ""
    if requirement is not None:
        requirement_yaml = f"requirements:\n  - kind: file_exists\n    path: {requirement}\n"
    return f'''version: 1
{requirement_yaml}steps:
  - id: check
    run: "{command}"
'''


def _plan(path: Path, imports: tuple[str, ...], *, with_value: str | None = None) -> None:
    lines = [
        "version: 1",
        "goons:",
    ]
    for ref in imports:
        lines.append(f"  - import: {ref}")
        if with_value is not None and ref == "general/a":
            lines += ["    with:", f"      python: '{with_value}'"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _input_atomic() -> str:
    return """version: 1
inputs:
  python:
    type: string
    default: "3.12"
steps:
  - id: check
    run: "echo ${{ inputs.python }}"
"""


def _resolve(plan_path: Path, goon_root: Path):
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(goon_root))
    graph = resolver.analyze_source(plan_path)
    return resolver.resolve_source_graph(graph), graph


def test_generation_is_deterministic_and_byte_identical(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))
    plan, graph = _resolve(plan_path, goons)
    first = build_execution_lock(plan)
    second = build_execution_lock(plan)
    assert first == second
    assert render_execution_lock(first) == render_execution_lock(second)
    assert parse_execution_lock(render_execution_lock(first)) == first


@pytest.mark.parametrize("duplicate", ["digest", "pin-commit", "merge", "cycle"])
def test_lock_yaml_rejects_duplicate_authority_instead_of_selecting_the_last_value(
    tmp_path: Path, duplicate: str
) -> None:
    from oh_goon.core.models.git_sources import LockedExecutionSource

    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    path = tmp_path / "plan.yml"
    _plan(path, ("general/a",))
    plan, _ = _resolve(path, goons)
    plan = plan.model_copy(
        update={
            "execution_sources": (
                LockedExecutionSource(
                    git="https://example.test/goons.git",
                    ref="main",
                    path="goons/general/a",
                    commit="a" * 40,
                ),
            )
        }
    )
    text = render_execution_lock(build_execution_lock(plan))
    assert parse_execution_lock(text).execution_sources[0].commit == "a" * 40
    if duplicate == "digest":
        text += "digest: sha256:" + "b" * 64 + "\n"
    elif duplicate == "pin-commit":
        text = text.replace(
            "  commit: " + "a" * 40, "  commit: " + "b" * 40 + "\n  commit: " + "a" * 40
        )
    elif duplicate == "merge":
        text = text.replace("lock_version: 2", "<<: {lock_version: 2}")
    else:
        text += "external: &cycle {recursive: *cycle}\n"
    with pytest.raises(FleetPlanInvalid, match="Malformed goon.lock"):
        parse_execution_lock(text)


def test_lock_format_version_does_not_coerce_yaml_booleans(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    path = tmp_path / "plan.yml"
    _plan(path, ("general/a",))
    plan, _ = _resolve(path, goons)
    text = render_execution_lock(build_execution_lock(plan))
    assert parse_execution_lock(text).lock_version == 2
    with pytest.raises(FleetPlanInvalid, match="Unsupported goon.lock version"):
        parse_execution_lock(text.replace("lock_version: 2", "lock_version: true"))


def test_generated_lock_over_owner_bound_refuses_before_publication(tmp_path, monkeypatch):
    from oh_goon.services import execution_lock_persistence

    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    path = tmp_path / "plan.yml"
    _plan(path, ("general/a",))
    plan, _ = _resolve(path, goons)
    lock = build_execution_lock(plan)
    rendered = render_execution_lock(lock)
    assert parse_execution_lock(rendered) == lock
    monkeypatch.setattr(
        execution_lock_persistence, "MAX_EXECUTION_LOCK_BYTES", len(rendered.encode("utf-8")) - 1
    )
    destination = tmp_path / "unpublished/goon.lock"
    with pytest.raises(FleetPlanInvalid, match="source-size bound"):
        execution_lock_persistence.write_execution_lock(destination, lock)
    assert not destination.parent.exists()


@pytest.mark.parametrize("changed", ["executable", "profile", "connection"])
def test_native_selection_identities_remain_fresh_across_alternating_values(
    tmp_path: Path, changed: str
) -> None:
    from dataclasses import replace

    from oh_goon.core.models.agent_providers import (
        AgentConnection,
        AgentModel,
        AgentProvider,
        AgentProvidersConfig,
        NativeAgentDefinition,
        NativeAgentProfile,
        ResolvedNativeAgent,
    )
    from oh_goon.core.models.execution_lock_models import ExecutionLock
    from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
    from tests.factories.agent_review import agent_review_case

    connection = AgentConnection.select(
        AgentProvidersConfig(
            default_model="approved",
            providers={
                "company": AgentProvider(
                    api="openai-chat-completions", base_url="http://127.0.0.1:12345/v1"
                )
            },
            models={
                "approved": AgentModel(
                    provider="company", model="synthetic-reviewer", parameters={"temperature": 0.25}
                )
            },
        )
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="approved"),
        executable_sha256="a" * 64,
        connection=connection,
    )
    if changed == "executable":
        another = replace(
            selected,
            definition=NativeAgentDefinition(executable="/native/another-pi"),
            executable_sha256="b" * 64,
        )
    elif changed == "profile":
        another = replace(
            selected, profile=selected.profile.model_copy(update={"timeout_seconds": 60.0})
        )
    else:
        another = replace(
            selected,
            connection=connection.model_copy(
                update={
                    "provider": connection.provider.model_copy(
                        update={"base_url": "http://127.0.0.1:12346/v1"}
                    ),
                    "selection": connection.selection.model_copy(
                        update={"parameters": {"temperature": 0.75}}
                    ),
                }
            ),
        )

    resolver, path, _, options = agent_review_case(
        tmp_path, plan_template="agents/native-review", native_agent=selected
    )
    options["inputs"] = {"repo": "local:repo", "skill": "local:skills/repair"}
    graph = resolver.analyze_source(path)
    plans: list[ResolvedFleetPlan] = []
    locks: list[ExecutionLock] = []
    for native in (selected, another, selected):
        options["native_agent"] = native
        plan, _, _ = resolver.resolve_frozen_agents(path, source_graph=graph, **options)
        restored = ResolvedFleetPlan.model_validate_json(plan.model_dump_json())
        assert restored == plan and restored.native_agent == native
        lock = build_execution_lock(restored)
        transported = parse_execution_lock(render_execution_lock(lock))
        verify_execution_lock(lock, transported)
        plans.append(restored)
        locks.append(lock)

    first, second, repeated = plans
    assert first.plan_id != second.plan_id
    assert locks[0].plan != locks[1].plan and locks[0].digest != locks[1].digest
    stale_plan = second.model_copy(update={"plan_id": first.plan_id})
    with pytest.raises(ValueError, match="plan_id must match"):
        ResolvedFleetPlan.model_validate_json(stale_plan.model_dump_json())
    with pytest.raises(FleetPlanInvalid, match="stale"):
        verify_execution_lock(locks[1], locks[0])
    assert repeated.model_dump_json() == first.model_dump_json()
    assert locks[2] == build_execution_lock(first) == locks[0]
    assert ResolvedFleetPlan.model_validate_json(first.model_dump_json()) == first


def test_configuration_is_provenance_not_executable_identity_and_is_source_bound(tmp_path):
    goons = tmp_path / "goons"
    _package(goons, "general/a", _input_atomic())
    path = tmp_path / "plan.yml"
    _plan(path, ("general/a",))
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(goons))
    plan = resolver.resolve(path)
    target = prepare_reviewed_execution_target(
        resolver, path, expected_execution_digest=build_execution_lock(plan).digest
    )
    without = target.model_copy(
        update={"plan": target.plan.model_copy(update={"configuration": None})}
    )
    assert target.plan.plan_id == without.plan.plan_id
    assert build_execution_lock(target.plan) == build_execution_lock(without.plan)
    assert operation_intent_key("execute-plan", target) == operation_intent_key(
        "execute-plan", without
    )
    payload = target.model_dump()
    payload["plan"]["configuration"]["root_source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="different source"):
        FleetPlanTarget.model_validate(payload)


def test_step_command_and_requirement_changes_invalidate_goon(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    source = _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))
    plan_path.write_text("execution: {purpose: cicd}\n" + plan_path.read_text(), encoding="utf-8")
    before_plan, before_graph = _resolve(plan_path, goons)
    before = build_execution_lock(before_plan)

    source.write_text(_atomic("a", command="false"), encoding="utf-8")
    after_plan, after_graph = _resolve(plan_path, goons)
    after = build_execution_lock(after_plan)
    assert before.execution[0].digest != after.execution[0].digest
    assert before.digest != after.digest

    source.write_text(_atomic("a", command="true", requirement="marker.txt"), encoding="utf-8")
    required_plan, required_graph = _resolve(plan_path, goons)
    required = build_execution_lock(required_plan)
    assert before.execution[0].digest != required.execution[0].digest


def test_plan_order_and_input_changes_invalidate_lock(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    _package(goons, "general/a", _input_atomic())
    _package(goons, "general/b", _atomic("b"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a", "general/b"), with_value="3.12")
    before_plan, before_graph = _resolve(plan_path, goons)
    before = build_execution_lock(before_plan)

    _plan(plan_path, ("general/b", "general/a"), with_value=None)
    reordered_plan, reordered_graph = _resolve(plan_path, goons)
    reordered = build_execution_lock(reordered_plan)
    assert [item.goon for item in before.execution] != [item.goon for item in reordered.execution]
    assert before.plan != reordered.plan

    _plan(plan_path, ("general/a",), with_value="3.13")
    input_plan, input_graph = _resolve(plan_path, goons)
    changed = build_execution_lock(input_plan)
    assert changed.execution[0].digest != before.execution[0].digest


def test_composition_change_invalidates_without_serializing_steps(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    _package(goons, "general/b", _atomic("b"))
    composite = _package(
        goons,
        "general/bundle",
        """version: 1
goons:
  - import: general/a
  - import: general/b
""",
    )
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/bundle",))
    before_plan, before_graph = _resolve(plan_path, goons)
    before = build_execution_lock(before_plan)
    composite.write_text(
        """version: 1
goons:
  - import: general/b
  - import: general/a
""",
        encoding="utf-8",
    )
    after_plan, after_graph = _resolve(plan_path, goons)
    after = build_execution_lock(after_plan)
    assert before.execution[0].digest != after.execution[0].digest
    rendered = render_execution_lock(after)
    assert "argv" not in rendered
    assert "step_id" not in rendered


def test_presentation_only_change_does_not_invalidate_semantic_lock(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    source = _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))
    before_plan, before_graph = _resolve(plan_path, goons)
    before = build_execution_lock(before_plan)
    before_source_sha = before_graph.root.source_sha256
    source.write_text(
        "# comment\n" + _atomic("a"),
        encoding="utf-8",
    )
    plan_path.write_text(
        "# plan comment\n" + plan_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    after_plan, after_graph = _resolve(plan_path, goons)
    after = build_execution_lock(after_plan)
    assert before == after
    assert before_source_sha != after_graph.root.source_sha256


def test_external_artifact_is_digest_only_and_size_independent(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))
    lockfile = tmp_path / "uv.lock"
    lockfile.write_bytes(b"a" * 100)
    plan, graph = _resolve(plan_path, goons)
    small = build_execution_lock(plan, external_artifacts=(("uv.lock", lockfile.read_bytes()),))
    small_text = render_execution_lock(small)
    lockfile.write_bytes(b"b" * 100_000)
    large = build_execution_lock(plan, external_artifacts=(("uv.lock", lockfile.read_bytes()),))
    large_text = render_execution_lock(large)
    assert small.external[0].digest != large.external[0].digest
    assert abs(len(small_text) - len(large_text)) < 10
    assert "aaaa" not in small_text and "bbbb" not in large_text


def test_malformed_version_tamper_and_stale_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(FleetPlanInvalid, match="Malformed goon.lock"):
        parse_execution_lock("not: [valid")
    with pytest.raises(FleetPlanInvalid, match="Unsupported goon.lock version"):
        parse_execution_lock("lock_version: 3\nplan: x\n")

    goons = tmp_path / "goons"
    source = _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))
    plan, graph = _resolve(plan_path, goons)
    expected = build_execution_lock(plan)
    tampered = expected.model_copy(update={"digest": "sha256:" + "0" * 64})
    with pytest.raises(FleetPlanInvalid, match="tampered"):
        verify_execution_lock(expected, tampered)

    source.write_text(_atomic("a", command="false"), encoding="utf-8")
    changed_plan, changed_graph = _resolve(plan_path, goons)
    changed = build_execution_lock(changed_plan)
    with pytest.raises(FleetPlanInvalid, match="stale"):
        verify_execution_lock(changed, expected)


def test_missing_lock_fails_closed_and_verified_object_reaches_run_target(tmp_path: Path) -> None:
    from typing import cast

    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.services.fleet_plans import FleetPlanService

    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))

    class RecordingResolver(FleetPlanResolver):
        last_resolved = None

        def resolve_source_graph(
            self, source_graph, *, extensions_path=None, inputs=None, repository=None
        ):
            plan = super().resolve_source_graph(
                source_graph, extensions_path=extensions_path, inputs=inputs, repository=repository
            )
            self.last_resolved = plan
            return plan

    class RecordingRuns:
        target = None

        def require_plan_ingress_preparation(self, fleet_run_id):
            # This fixture records lock-target transport; the real admission
            # lifecycle and hostile enrollment paths have separate tests.
            assert fleet_run_id == "test-lock-run"

        def start_prepared_plan(self, target, **kwargs):
            self.target = target
            return object()

    resolver = RecordingResolver(goon_templates=GoonTemplateStore(goons))
    runs = RecordingRuns()
    service = FleetPlanService(
        resolver,
        cast(object, runs),
        cast(object, object()),
        cast(object, object()),
        cast(object, object()),
    )

    with pytest.raises(FleetPlanInvalid, match="requires goon.lock"):
        service.run(plan_path, repository_ids={"repo-a"}, fleet_run_id="test-lock-run")

    service.lock(plan_path)
    service.run(plan_path, repository_ids={"repo-a"}, fleet_run_id="test-lock-run")
    target = cast(FleetPlanTarget, runs.target)
    assert target.plan is resolver.last_resolved
    assert target.execution_digest is not None
    assert target.execution_digest.startswith("sha256:")


def test_explicit_lock_path_verifies_same_execution_without_source_adjacent_lock(
    tmp_path: Path,
) -> None:
    from typing import cast

    from oh_goon.services.fleet_plans import FleetPlanService

    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "source/plan.yml"
    plan_path.parent.mkdir(parents=True)
    _plan(plan_path, ("general/a",))
    external_lock = tmp_path / "state/locks/app.goon.lock"

    class RecordingRuns:
        target = None

        def require_plan_ingress_preparation(self, fleet_run_id):
            # This fixture records lock-target transport; the real admission
            # lifecycle and hostile enrollment paths have separate tests.
            assert fleet_run_id == "test-lock-run"

        def start_prepared_plan(self, target, **kwargs):
            self.target = target
            return object()

    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(goons))
    runs = RecordingRuns()
    service = FleetPlanService(
        resolver,
        cast(object, runs),
        cast(object, object()),
        cast(object, object()),
        cast(object, object()),
    )

    service.lock(plan_path, output=external_lock)
    assert external_lock.is_file()
    assert not (plan_path.parent / "goon.lock").exists()
    service.run(
        plan_path, repository_ids={"repo-a"}, lock_path=external_lock, fleet_run_id="test-lock-run"
    )
    assert runs.target is not None


def test_more_steps_change_digest_without_material_lock_growth(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    source = _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))
    plan, graph = _resolve(plan_path, goons)
    small = render_execution_lock(build_execution_lock(plan))

    steps = "".join(f"""  - id: check-{index}\n    run: "echo {index}"\n""" for index in range(30))
    source.write_text(
        "version: 1\nsteps:\n" + steps,
        encoding="utf-8",
    )
    many_plan, many_graph = _resolve(plan_path, goons)
    large = render_execution_lock(build_execution_lock(many_plan))
    assert small != large
    assert abs(len(small) - len(large)) < 20
    assert "check-29" not in large


def test_shared_run_target_count_is_not_part_of_execution_lock(tmp_path: Path) -> None:
    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    plan_path = tmp_path / "plan.yml"
    _plan(plan_path, ("general/a",))
    plan, graph = _resolve(plan_path, goons)
    lock = render_execution_lock(build_execution_lock(plan))
    one_target = {"repo-1"}
    five_thousand_targets = {f"repo-{index}" for index in range(5000)}
    assert one_target != five_thousand_targets
    assert render_execution_lock(build_execution_lock(plan)) == lock


def test_frozen_external_identities_reconstruct_the_same_canonical_lock(tmp_path):
    from oh_goon.core.models.execution_lock_models import LockedExternalArtifact

    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    path = tmp_path / "plan.yml"
    _plan(path, ("general/a",))
    plan, _ = _resolve(path, goons)
    original = build_execution_lock(
        plan, external_artifacts=(("z/receipt", b"receipt"), ("workflow/run.yml", b"selection"))
    )
    transported = tuple(reversed(original.external))
    assert build_execution_lock(plan, external_identities=transported) == original
    alias = LockedExternalArtifact(path="workflow\\run.yml", digest=original.external[0].digest)
    assert build_execution_lock(plan, external_identities=(alias, original.external[1])) == original
    with pytest.raises(FleetPlanInvalid, match="never both"):
        build_execution_lock(
            plan,
            external_artifacts=(("run.yml", b"selection"),),
            external_identities=original.external,
        )
    with pytest.raises(FleetPlanInvalid, match="unique"):
        build_execution_lock(plan, external_identities=(original.external[0], alias))


@pytest.mark.parametrize("scheduled", [False, True])
def test_target_binds_full_lock_and_exact_live_or_scheduled_config_receipt(tmp_path, scheduled):
    import hashlib

    from oh_goon.core.models.caller_input_file import (
        CallerInputFileIdentity,
        ScheduledCallerFileReceipt,
    )
    from oh_goon.core.models.execution_identity import ExecutionIdentity
    from oh_goon.core.models.execution_lock_models import LockedExternalArtifact

    goons = tmp_path / "goons"
    _package(goons, "general/a", _atomic("a"))
    path = tmp_path / "plan.yml"
    _plan(path, ("general/a",))
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(goons))
    plan = resolver.resolve(path)
    base = prepare_reviewed_execution_target(
        resolver, path, expected_execution_digest=build_execution_lock(plan).digest
    )
    selection = b"one exact captured configuration"
    sha = hashlib.sha256(selection).hexdigest()
    full = build_execution_lock(plan, external_artifacts=(("run.yml", selection),))
    if scheduled:
        receipt = {
            "scheduled_caller_file": ScheduledCallerFileReceipt(
                relative_path="run.yml", sha256=sha, execution_digest=full.digest
            )
        }
    else:
        receipt = {
            "caller_input_file": CallerInputFileIdentity(
                caller_root=tmp_path,
                relative_path="run.yml",
                sha256=sha,
                source_device=1,
                source_inode=2,
            )
        }
    values = (
        base.model_dump()
        | receipt
        | {"identity": ExecutionIdentity(digest=full.digest), "lock_artifacts": full.external}
    )
    target = FleetPlanTarget.model_validate(values)
    assert target.execution_digest == full.digest
    assert FleetPlanTarget.model_validate_json(target.model_dump_json()) == target
    raw = dict(values, identity=base.identity)
    with pytest.raises(ValueError, match="resolved Plan semantics"):
        FleetPlanTarget.model_validate(raw)
    forged = LockedExternalArtifact(path="run.yml", digest="sha256:" + "a" * 64)
    altered = dict(
        values,
        identity=ExecutionIdentity(
            digest=build_execution_lock(plan, external_identities=(forged,)).digest
        ),
        lock_artifacts=(forged,),
    )
    with pytest.raises(ValueError, match="locked external artifact"):
        FleetPlanTarget.model_validate(altered)
    missing = LockedExternalArtifact(path="other.yml", digest=full.external[0].digest)
    altered = dict(
        values,
        identity=ExecutionIdentity(
            digest=build_execution_lock(plan, external_identities=(missing,)).digest
        ),
        lock_artifacts=(missing,),
    )
    with pytest.raises(ValueError, match="locked external artifact"):
        FleetPlanTarget.model_validate(altered)
    # Retained targets that predate config artifact custody retain their original
    # semantics and serialization; no historical Run bytes are rewritten.
    assert "lock_artifacts" not in base.model_dump(mode="json")
    assert FleetPlanTarget.model_validate_json(base.model_dump_json()) == base
