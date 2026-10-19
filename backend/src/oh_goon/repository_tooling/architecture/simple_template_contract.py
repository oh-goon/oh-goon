from __future__ import annotations

import re
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceNotFound
from oh_goon.core.models.fleet_plan import (
    CommandPlanStep,
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
)
from oh_goon.core.template_identity import template_classification, template_reference_allowed
from oh_goon.integrations.fleet_plan_compilation import (
    ParsedFleetYamlDocument,
    compile_goon_document,
    compile_plan_document,
    parse_fleet_yaml_document,
)
from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import (
    GOON_TEMPLATES_ROOT,
    PLAN_TEMPLATES_ROOT,
)
from oh_goon.runtime.template_discovery import template_readme_description


def check_simple_template_contract() -> list[Violation]:
    """Check shipped-template policy after production Fleet compilation.

    Universal Fleet YAML meaning belongs to ``fleet_plan_compilation``. This architecture
    check deliberately retains only policy that is stronger for the shipped canonical
    catalog than for the portable authoring language itself.
    """

    violations: list[Violation] = []
    for path in sorted(GOON_TEMPLATES_ROOT.glob("*/*/goon.yml")):
        document = _document(path, violations)
        if document is None:
            continue
        template = _compile_goon(path, document, violations)
        if template is None:
            continue
        _check_shipped_goon_policy(path, document, template, violations)

    for path in sorted(PLAN_TEMPLATES_ROOT.glob("*/*/plan.yml")):
        document = _document(path, violations)
        if document is None:
            continue
        template = _compile_plan(path, document, violations)
        if template is None:
            continue
        _check_shipped_plan_policy(path, document, template, violations)
    return violations


def _document(path: Path, violations: list[Violation]) -> ParsedFleetYamlDocument | None:
    try:
        content = path.read_text(encoding="utf-8")
        return parse_fleet_yaml_document(content, source=str(path))
    except (OSError, FleetPlanInvalid) as exc:
        violations.append(Violation(path, str(exc)))
        return None


def _compile_goon(
    path: Path,
    document: ParsedFleetYamlDocument,
    violations: list[Violation],
) -> CompiledExecutionTemplate | None:
    try:
        return compile_goon_document(
            document,
            source=str(path),
            expected_name=path.parent.name,
        )
    except FleetPlanInvalid as exc:
        violations.append(Violation(path, str(exc)))
        return None


def _compile_plan(
    path: Path,
    document: ParsedFleetYamlDocument,
    violations: list[Violation],
) -> CompiledExecutionTemplate | None:
    try:
        return compile_plan_document(
            document,
            source=str(path),
            expected_name=path.parent.name,
        )
    except FleetPlanInvalid as exc:
        violations.append(Violation(path, str(exc)))
        return None


def _check_shipped_goon_policy(
    path: Path,
    document: ParsedFleetYamlDocument,
    template: CompiledExecutionTemplate,
    violations: list[Violation],
) -> None:
    redundant = sorted(set(document.payload) & {"name", "description"})
    if redundant:
        violations.append(
            Violation(
                path,
                "Shipped Goon YAML must not declare redundant "
                f"{', '.join(redundant)}; package path owns machine identity and README.md owns human description",
            )
        )
    _check_catalog_classification(path, template, kind="Goon", violations=violations)
    _check_shipped_entrypoint_authority(path, template, violations)
    _check_shipped_shell_authority(path, document, template, violations)
    _check_readme_goon_structure(path, template, violations)


def _check_shipped_entrypoint_authority(
    path: Path,
    template: CompiledExecutionTemplate,
    violations: list[Violation],
) -> None:
    """Keep repository Python entrypoints independent of incidental virtualenv layout."""

    for step in template.steps:
        if not isinstance(step, CommandPlanStep) or not step.argv:
            continue
        executable = step.argv[0].replace("\\", "/")
        if "/.venv/bin/" not in f"/{executable.lstrip('/')}" and not executable.startswith(
            ".venv/bin/"
        ):
            continue
        violations.append(
            Violation(
                path,
                "Shipped Goon commands must not bind execution authority to .venv/bin paths; "
                "repository Python commands must resolve through project/uv authority",
            )
        )


def _check_shipped_shell_authority(
    path: Path,
    document: ParsedFleetYamlDocument,
    template: CompiledExecutionTemplate,
    violations: list[Violation],
) -> None:
    """Direct argv needs no shell; exceptional opaque child authority needs written justification."""
    execution = document.payload.get("execution")
    if not isinstance(execution, dict) or "shell" not in execution:
        return
    shell = execution["shell"]
    try:
        readme = (path.parent / "README.md").read_text(encoding="utf-8")
    except OSError:
        readme = ""
    if f"Shell authority (`{shell}`):" not in readme:
        violations.append(
            Violation(
                path,
                "Shipped explicit execution.shell requires a README Shell authority "
                "reason identifying real shell semantics or opaque child-tool authority; "
                "direct run argv alone does not require a shell",
            )
        )


def _check_shipped_plan_policy(
    path: Path,
    document: ParsedFleetYamlDocument,
    template: CompiledExecutionTemplate,
    violations: list[Violation],
) -> None:
    execution = document.payload.get("execution")
    if isinstance(execution, dict) and "resources" in execution:
        violations.append(
            Violation(
                path,
                "Shipped Plan templates must not author fixed resource caps; omitted caps inherit "
                "the selected executor capacity and effective policy is exposed by Plan inspection",
            )
        )
    _check_catalog_classification(path, template, kind="Plan", violations=violations)
    _check_readme_plan_contract(path, template, violations)


def _check_catalog_classification(
    path: Path,
    template: CompiledExecutionTemplate,
    *,
    kind: str,
    violations: list[Violation],
) -> None:
    key = f"{path.parent.parent.name}/{path.parent.name}"
    try:
        owner = template_classification(key, kind=kind)
    except FleetPlanInvalid as exc:
        violations.append(Violation(path, str(exc)))
        return

    for step in template.steps:
        if not isinstance(step, CompiledGoonImportStep):
            continue
        try:
            imported = template_classification(step.goon, kind="Goon")
        except FleetPlanInvalid as exc:
            violations.append(Violation(path, str(exc)))
            continue
        if template_reference_allowed(owner, imported):
            continue
        violations.append(
            Violation(
                path,
                f"{owner.value} {kind} {key} cannot reference {imported.value} Goon {step.goon}",
            )
        )


def _check_readme_goon_structure(
    path: Path,
    template: CompiledExecutionTemplate,
    violations: list[Violation],
) -> None:
    """Keep human structural claims aligned with the production compiler.

    README structure wording is explanatory, not machine authority. When a shipped
    Goon README chooses to call itself Atomic or Composite, that claim must project
    the structure already owned by the compiled executable contract.
    """

    readme = path.parent / "README.md"
    if not readme.is_file() or readme.is_symlink():
        # Package shape/readability has its own architecture owner. This check only
        # converges a human claim when one exists.
        return
    try:
        description = template_readme_description(path.parent, kind="Goon")
    except FleetPlanSourceNotFound as exc:
        violations.append(Violation(readme, str(exc)))
        return

    declared = tuple(
        label
        for label in ("Atomic", "Composite")
        if re.search(rf"\b{label}\s+Goons?\b", description, flags=re.IGNORECASE)
    )
    if not declared:
        return

    is_composite = all(isinstance(step, CompiledGoonImportStep) for step in template.steps)
    expected = "Composite" if is_composite else "Atomic"
    if declared == (expected,):
        return

    claim = " and ".join(f"{label} Goon" for label in declared)
    violations.append(
        Violation(
            path.parent / "README.md",
            f"README.md classifies {path.parent.parent.name}/{path.parent.name} as {claim}, "
            f"but the production compiler resolves it as {expected} Goon",
        )
    )


def _check_readme_plan_contract(
    path: Path,
    template: CompiledExecutionTemplate,
    violations: list[Violation],
) -> None:
    """Keep optional README projections subordinate to the compiled Plan contract.

    README prose may explain intent, but it must not become a second execution/resource
    policy owner. When a README chooses to expose the compact ``Inputs`` contract field,
    that field is a projection of the production compiler and must remain exact.
    """

    readme = path.parent / "README.md"
    if not readme.is_file() or readme.is_symlink():
        return
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError as exc:
        violations.append(Violation(readme, f"Cannot read template README: {exc}"))
        return

    contract_fields = _readme_contract_fields(text)
    if "execution" in contract_fields:
        violations.append(
            Violation(
                readme,
                "README.md must not duplicate Plan execution/resource policy; the compiled Plan "
                "and admission/inspection evidence are the machine authority",
            )
        )

    inputs = contract_fields.get("inputs")
    if inputs is not None:
        declared = _readme_input_projection(inputs)
        expected = template.projected_inputs
        if declared != expected:
            violations.append(
                Violation(
                    readme,
                    "README.md Inputs projection does not match the production compiler: "
                    f"declared={declared!r} resolved={expected!r}",
                )
            )

    if _readme_asserts_fixed_resource_values(text):
        violations.append(
            Violation(
                readme,
                "README.md must not publish fixed CPU/memory/process values as Plan policy; "
                "resource caps are machine-owned and omitted caps inherit admitted executor capacity",
            )
        )


def _readme_contract_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        match = re.match(r"^\s*-\s+([A-Za-z][A-Za-z -]*):\s*(.*?)\s*$", raw_line)
        if match is None:
            continue
        fields[match.group(1).strip().casefold()] = match.group(2)
    return fields


def _readme_input_projection(value: str) -> tuple[str, ...]:
    if value.strip().casefold() == "none":
        return ()
    return tuple(re.findall(r"`([^`]+)`", value))


def _readme_asserts_fixed_resource_values(text: str) -> bool:
    patterns = (
        r"\b\d+\s+CPU\s+millicores\b",
        r"\b\d+\s+MiB\s+memory\b",
        r"\bmax\s+`?\d+`?\s+processes\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
