from pathlib import Path

from oh_goon.repository_tooling.architecture import simple_template_contract


def _roots(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    goons = tmp_path / "goons"
    plans = tmp_path / "plans"
    goons.mkdir()
    plans.mkdir()
    monkeypatch.setattr(simple_template_contract, "GOON_TEMPLATES_ROOT", goons)
    monkeypatch.setattr(simple_template_contract, "PLAN_TEMPLATES_ROOT", plans)
    return goons, plans


def test_shipped_catalog_satisfies_simple_contract() -> None:
    assert simple_template_contract.check_simple_template_contract() == []


def test_simple_contract_consumes_production_compiler_for_universal_goon_semantics(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "python" / "bad"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\n"
        "name: bad\n"
        "description: invalid authoring\n"
        "steps:\n"
        "- uses: command\n"
        "  argv: [python, -c, print(1)]\n",
        encoding="utf-8",
    )

    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]

    assert len(messages) == 1
    assert "internal execution fields" in messages[0]


def test_simple_contract_does_not_reinterpret_safe_run_arguments(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "python" / "safe"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\nsteps:\n- run: 'echo \"python -c\"'\n",
        encoding="utf-8",
    )

    assert simple_template_contract.check_simple_template_contract() == []


def test_simple_contract_rejects_redundant_shipped_goon_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "python" / "redundant"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\nname: redundant\ndescription: duplicated docs\nsteps:\n- run: echo ok\n",
        encoding="utf-8",
    )

    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]

    assert len(messages) == 1
    assert "package path owns machine identity" in messages[0]
    assert "README.md owns human description" in messages[0]


def test_simple_contract_rejects_incidental_virtualenv_entrypoint_authority(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "oh-goon" / "entrypoint-probe"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\nsteps:\n- run: backend/.venv/bin/repository-command check\n",
        encoding="utf-8",
    )

    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]

    assert messages == [
        "Shipped Goon commands must not bind execution authority to .venv/bin paths; "
        "repository Python commands must resolve through project/uv authority"
    ]


def test_simple_contract_rejects_unclassified_shipped_category(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "experimental" / "new-capability"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\nsteps:\n- run: echo ok\n",
        encoding="utf-8",
    )

    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]

    assert messages == ["Goon category 'experimental' has no shipped template classification"]


def test_production_template_cannot_reference_teaching_example(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "python" / "bad-reference"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\ngoons:\n- advanced/isolated-dataflow\n",
        encoding="utf-8",
    )

    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]

    assert messages == [
        "production Goon python/bad-reference cannot reference "
        "teaching-example Goon advanced/isolated-dataflow"
    ]


def test_teaching_example_may_reuse_production_capability(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "advanced" / "example"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\ngoons:\n- python/quality\n",
        encoding="utf-8",
    )

    assert simple_template_contract.check_simple_template_contract() == []


def test_simple_contract_rejects_resource_overrides_even_for_app_certification(
    tmp_path: Path, monkeypatch
) -> None:
    _goons, plans = _roots(tmp_path, monkeypatch)
    certification = plans / "oh-goon" / "app-certification"
    ordinary = plans / "python" / "standard"
    certification.mkdir(parents=True)
    ordinary.mkdir(parents=True)
    body = "version: 1\nexecution:\n  resources:\n    memory_mib: 4096\ngoons:\n- python/quality\n"
    (certification / "plan.yml").write_text(body, encoding="utf-8")
    (ordinary / "plan.yml").write_text(body, encoding="utf-8")

    violations = simple_template_contract.check_simple_template_contract()

    assert {violation.path for violation in violations} == {
        certification / "plan.yml",
        ordinary / "plan.yml",
    }
    assert all("omitted caps inherit the selected executor capacity" in violation.message for violation in violations)


def test_plan_readme_cannot_duplicate_execution_or_fixed_resource_policy(
    tmp_path: Path, monkeypatch
) -> None:
    _goons, plans = _roots(tmp_path, monkeypatch)
    package = plans / "python" / "policy-probe"
    package.mkdir(parents=True)
    (package / "plan.yml").write_text(
        "version: 1\ngoons:\n- python/quality\n",
        encoding="utf-8",
    )
    (package / "README.md").write_text(
        "# python/policy-probe\n\n"
        "## Contract\n\n"
        "- Execution: strict `cicd`, `4000` CPU millicores, `8192` MiB memory, max `256` processes.\n",
        encoding="utf-8",
    )

    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]

    assert any("must not duplicate Plan execution/resource policy" in message for message in messages)
    assert any("must not publish fixed CPU/memory/process values" in message for message in messages)


def test_plan_readme_inputs_are_a_compiler_projection_not_a_second_schema(
    tmp_path: Path, monkeypatch
) -> None:
    _goons, plans = _roots(tmp_path, monkeypatch)
    package = plans / "python" / "input-probe"
    package.mkdir(parents=True)
    (package / "plan.yml").write_text(
        "version: 1\ninputs:\n- coverage_threshold\ngoons:\n- python/quality\n",
        encoding="utf-8",
    )
    (package / "README.md").write_text(
        "# python/input-probe\n\n## Contract\n\n- Inputs: `stale_name`.\n",
        encoding="utf-8",
    )

    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]

    assert messages == [
        "README.md Inputs projection does not match the production compiler: "
        "declared=('stale_name',) resolved=('coverage_threshold',)"
    ]


def test_plan_readme_can_explain_machine_policy_without_copying_values(
    tmp_path: Path, monkeypatch
) -> None:
    _goons, plans = _roots(tmp_path, monkeypatch)
    package = plans / "python" / "explanation-probe"
    package.mkdir(parents=True)
    (package / "plan.yml").write_text(
        "version: 1\ninputs:\n- coverage_threshold\ngoons:\n- python/quality\n",
        encoding="utf-8",
    )
    (package / "README.md").write_text(
        "# python/explanation-probe\n\n"
        "## Contract\n\n"
        "- Inputs: `coverage_threshold`.\n"
        "- Effective execution/resource policy is machine-owned and visible through Plan inspection.\n",
        encoding="utf-8",
    )

    assert simple_template_contract.check_simple_template_contract() == []


def test_readme_structure_claim_must_match_compiled_goon_structure(
    tmp_path: Path, monkeypatch
) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)

    composite = goons / "python" / "structure-probe"
    composite.mkdir(parents=True)
    (composite / "goon.yml").write_text(
        "version: 1\ngoons:\n- python/quality\n",
        encoding="utf-8",
    )
    (composite / "README.md").write_text(
        "# python/structure-probe\n\n**Production template. atomic goon.**\n",
        encoding="utf-8",
    )

    atomic = goons / "security" / "other-probe"
    atomic.mkdir(parents=True)
    (atomic / "goon.yml").write_text(
        "version: 1\nsteps:\n- run: echo ok\n",
        encoding="utf-8",
    )
    (atomic / "README.md").write_text(
        "# security/other-probe\n\n**Production template. Composite Goon.**\n",
        encoding="utf-8",
    )

    violations = simple_template_contract.check_simple_template_contract()

    assert {violation.message for violation in violations} == {
        "README.md classifies python/structure-probe as Atomic Goon, "
        "but the production compiler resolves it as Composite Goon",
        "README.md classifies security/other-probe as Composite Goon, "
        "but the production compiler resolves it as Atomic Goon",
    }


def test_shipped_structural_claims_are_generic_compiler_projections() -> None:
    violations = simple_template_contract.check_simple_template_contract()

    assert not [
        violation
        for violation in violations
        if "production compiler resolves it as" in violation.message
    ]


def test_explicit_shell_requires_documented_semantic_reason(tmp_path: Path, monkeypatch) -> None:
    goons, _plans = _roots(tmp_path, monkeypatch)
    package = goons / "oh-goon" / "shell-probe"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        "version: 1\nexecution:\n  shell: sh\nsteps:\n- run: make verify-p0\n",
        encoding="utf-8",
    )
    (package / "README.md").write_text("# shell probe\n", encoding="utf-8")
    messages = [v.message for v in simple_template_contract.check_simple_template_contract()]
    assert len(messages) == 1
    assert "direct run argv alone does not require a shell" in messages[0]

    (package / "README.md").write_text(
        "# shell probe\n\nShell authority (`sh`): nested fixture explicitly invokes sh.\n",
        encoding="utf-8",
    )
    assert simple_template_contract.check_simple_template_contract() == []


def test_shipped_shell_audit_retains_only_proven_opaque_child_authority() -> None:
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    import yaml
    store = GoonTemplateStore()
    explicit = {
        key: yaml.safe_load(store.source(key).content)["execution"]["shell"]
        for key in store.keys()
        if isinstance(yaml.safe_load(store.source(key).content).get("execution"), dict)
        and "shell" in yaml.safe_load(store.source(key).content)["execution"]
    }
    assert explicit == {"oh-goon/backend-p0": "sh", "oh-goon/tooling-tests": "bash"}
