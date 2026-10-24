from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_migration import (
    GitLabGoonMigration,
    GitLabImportIssue,
    GitLabResolvedComponent,
)
from oh_goon.core.models.fleet_plan_source_document import FleetPlanSourceDocument
from oh_goon.integrations.fleet_plan_source import commit_plan_source
from oh_goon.integrations.gitlab_ci_compile import compile_native_goon, native_goon_yaml
from oh_goon.integrations.gitlab_ci_component import GitLabComponentFetcher

_RESERVED = {
    "stages",
    "variables",
    "workflow",
    "default",
    "include",
    "image",
    "services",
    "cache",
    "before_script",
    "after_script",
    "spec",
}
_IMPORT_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_COMPONENT_INPUT = re.compile(r"\$\[\[\s*inputs\.([A-Za-z_][A-Za-z0-9_-]*)\s*\]\]")
_COMPONENT_CONTEXT = re.compile(r"\$\[\[\s*component\.([A-Za-z_][A-Za-z0-9_-]*)\s*\]\]")


class GitLabCiGoonMigrator:
    """One-way GitLab CI -> one native Atomic repository Goon; never a runtime engine."""

    def __init__(self, component_fetcher: GitLabComponentFetcher) -> None:
        self._components = component_fetcher

    def migrate(
        self,
        source: Path,
        *,
        import_variables: dict[str, str] | None = None,
        token: str | None = None,
    ) -> GitLabGoonMigration:
        source = source.expanduser().resolve()
        root = _load_mapping(source)
        import_vars = import_variables or {}
        issues: list[GitLabImportIssue] = []
        components: list[GitLabResolvedComponent] = []
        documents: list[tuple[str, dict[str, Any]]] = [(source.name, root)]

        includes = root.get("include", [])
        if isinstance(includes, (str, dict)):
            includes = [includes]
        if includes is None:
            includes = []
        if not isinstance(includes, list):
            issues.append(_unsupported("", "include", "include must be a list or mapping"))
            includes = []
        for index, include in enumerate(includes, start=1):
            if not isinstance(include, dict) or set(include) - {"component", "inputs"}:
                issues.append(
                    _unsupported(
                        "",
                        f"include[{index}]",
                        "only include:component is supported by one-way import",
                    )
                )
                continue
            raw_reference = include.get("component")
            if not isinstance(raw_reference, str):
                issues.append(
                    _unsupported(
                        "", f"include[{index}].component", "component reference must be a string"
                    )
                )
                continue
            try:
                reference = _resolve_import_vars(raw_reference, import_vars)
                fetched = self._components.fetch(reference, token=token)
                component_body = _parse_component(
                    fetched.content,
                    include.get("inputs", {}),
                    import_vars=import_vars,
                    metadata=fetched.metadata,
                    issues=issues,
                )
            except FleetPlanInvalid as exc:
                issues.append(_unsupported("", f"include[{index}]", str(exc)))
                continue
            components.append(fetched.metadata)
            documents.append((fetched.metadata.reference, component_body))

        merged = _merge_documents(documents, issues)
        goon = compile_native_goon(
            source=source,
            payload=merged,
            issues=issues,
        )
        return GitLabGoonMigration(
            source=str(source),
            goon=goon,
            components=tuple(components),
            issues=tuple(issues),
        )

    @staticmethod
    def write_goon(
        path: Path,
        migration: GitLabGoonMigration,
        *,
        expected_revision: str | None = None,
    ) -> FleetPlanSourceDocument:
        if not migration.ready:
            raise FleetPlanInvalid(
                "GitLab import has unsupported semantics; Goon YAML was not written"
            )
        target = path.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return commit_plan_source(
            target.parent,
            target.name,
            expected_revision=expected_revision,
            content=native_goon_yaml(migration),
        )


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FleetPlanInvalid(f"Cannot read GitLab CI YAML {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FleetPlanInvalid("GitLab CI document must contain a top-level mapping")
    return payload


def _parse_component(
    content: str,
    supplied_inputs: Any,
    *,
    import_vars: dict[str, str],
    metadata: GitLabResolvedComponent,
    issues: list[GitLabImportIssue],
) -> dict[str, Any]:
    try:
        docs = [item for item in yaml.safe_load_all(content) if item is not None]
    except yaml.YAMLError as exc:
        raise FleetPlanInvalid(f"Invalid component YAML {metadata.reference}: {exc}") from exc
    if not docs or not all(isinstance(item, dict) for item in docs):
        raise FleetPlanInvalid(f"GitLab component {metadata.reference} must contain YAML mappings")
    header: dict[str, Any] = {}
    body: dict[str, Any]
    if "spec" in docs[0]:
        header = docs[0]
        body = docs[1] if len(docs) > 1 else {k: v for k, v in docs[0].items() if k != "spec"}
        if len(docs) > 2:
            raise FleetPlanInvalid(
                f"GitLab component {metadata.reference} has more than two YAML documents"
            )
    else:
        body = docs[0]
    if "include" in body:
        issues.append(
            _unsupported(
                "",
                "component.include",
                f"nested component includes are not imported: {metadata.reference}",
            )
        )
    bindings = _component_input_bindings(
        header.get("spec"), supplied_inputs, import_vars, metadata.reference
    )
    context = {
        "name": metadata.component,
        "version": metadata.revision,
        "reference": metadata.reference,
    }
    if re.fullmatch(r"[0-9a-fA-F]{40}", metadata.revision):
        context["sha"] = metadata.revision.lower()
    return cast(dict[str, Any], _interpolate_component(body, bindings, context, metadata.reference))


def _component_input_bindings(
    spec: Any,
    supplied: Any,
    import_vars: dict[str, str],
    reference: str,
) -> dict[str, Any]:
    spec = spec or {}
    if not isinstance(spec, dict):
        raise FleetPlanInvalid(f"GitLab component {reference} spec must be a mapping")
    definitions = spec.get("inputs", {}) or {}
    if not isinstance(definitions, dict):
        raise FleetPlanInvalid(f"GitLab component {reference} spec:inputs must be a mapping")
    supplied = supplied or {}
    if not isinstance(supplied, dict):
        raise FleetPlanInvalid(f"GitLab component {reference} include inputs must be a mapping")
    unknown = sorted(set(supplied) - set(definitions))
    if unknown:
        raise FleetPlanInvalid(
            f"GitLab component {reference} received unknown inputs: {', '.join(unknown)}"
        )
    result: dict[str, Any] = {}
    for name, raw_definition in definitions.items():
        definition = raw_definition or {}
        if not isinstance(definition, dict):
            raise FleetPlanInvalid(
                f"GitLab component input {name} in {reference} must be a mapping"
            )
        if "regex" in definition:
            raise FleetPlanInvalid(
                f"GitLab component input regex is not supported for {reference}:{name}"
            )
        value = supplied.get(name, definition.get("default"))
        if value is None and "default" not in definition:
            raise FleetPlanInvalid(f"GitLab component {reference} requires input {name}")
        value = _resolve_import_value(value, import_vars)
        input_type = definition.get("type", "string")
        if input_type == "boolean" and type(value) is not bool:
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                value = value.lower() == "true"
            else:
                raise FleetPlanInvalid(f"GitLab component input {reference}:{name} must be boolean")
        elif input_type == "number":
            if type(value) is not int:
                raise FleetPlanInvalid(
                    f"GitLab numeric input {reference}:{name} must be an integer for Fleet import"
                )
        elif input_type not in {"string", "boolean", "number"}:
            raise FleetPlanInvalid(
                f"GitLab component input type {input_type!r} is unsupported for {reference}:{name}"
            )
        options = definition.get("options")
        if options is not None and (not isinstance(options, list) or value not in options):
            raise FleetPlanInvalid(
                f"GitLab component input {reference}:{name} is not one of its declared options"
            )
        result[name] = value
    return result


def _interpolate_component(
    value: Any, inputs: dict[str, Any], context: dict[str, str], reference: str
) -> Any:
    if isinstance(value, str):
        full = _COMPONENT_INPUT.fullmatch(value)
        if full:
            name = full.group(1)
            if name not in inputs:
                raise FleetPlanInvalid(
                    f"GitLab component {reference} references undeclared input {name}"
                )
            return inputs[name]
        full_context = _COMPONENT_CONTEXT.fullmatch(value)
        if full_context:
            field = full_context.group(1)
            if field not in context:
                raise FleetPlanInvalid(
                    f"GitLab component context field {field} is unavailable for {reference}"
                )
            return context[field]
        value = _COMPONENT_INPUT.sub(
            lambda m: _component_text(inputs, m.group(1), reference), value
        )
        value = _COMPONENT_CONTEXT.sub(
            lambda m: _context_text(context, m.group(1), reference), value
        )
        return value
    if isinstance(value, list):
        return [_interpolate_component(item, inputs, context, reference) for item in value]
    if isinstance(value, dict):
        return {
            _interpolate_component(key, inputs, context, reference)
            if isinstance(key, str)
            else key: _interpolate_component(item, inputs, context, reference)
            for key, item in value.items()
        }
    return value


def _component_text(inputs: dict[str, Any], name: str, reference: str) -> str:
    if name not in inputs:
        raise FleetPlanInvalid(f"GitLab component {reference} references undeclared input {name}")
    value = inputs[name]
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


def _context_text(context: dict[str, str], field: str, reference: str) -> str:
    if field not in context:
        raise FleetPlanInvalid(
            f"GitLab component context field {field} is unavailable for {reference}"
        )
    return context[field]


def _resolve_import_vars(value: str, variables: dict[str, str]) -> str:
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name not in variables:
            missing.add(name)
            return match.group(0)
        return variables[name]

    result = _IMPORT_VAR.sub(replace, value)
    if missing:
        raise FleetPlanInvalid(
            f"GitLab import requires --variable for: {', '.join(sorted(missing))}"
        )
    return result


def _resolve_import_value(value: Any, variables: dict[str, str]) -> Any:
    return _resolve_import_vars(value, variables) if isinstance(value, str) else value


def _merge_documents(
    documents: list[tuple[str, dict[str, Any]]],
    issues: list[GitLabImportIssue],
) -> dict[str, Any]:
    merged: dict[str, Any] = {"stages": []}
    jobs: dict[str, Any] = {}
    for source, payload in documents:
        stages = payload.get("stages")
        if stages is not None:
            if not isinstance(stages, list) or not all(isinstance(item, str) for item in stages):
                issues.append(_unsupported("", "stages", f"invalid stages in {source}"))
            else:
                for stage in stages:
                    if stage not in merged["stages"]:
                        merged["stages"].append(stage)
        variables = payload.get("variables")
        if variables not in (None, {}):
            issues.append(
                _unsupported(
                    "",
                    "variables",
                    f"GitLab variables in {source} are ambient job exports; rewrite them as explicit Fleet env/input dataflow",
                )
            )
        for global_field in (
            "workflow",
            "image",
            "services",
            "cache",
            "before_script",
            "after_script",
        ):
            if global_field in payload:
                issues.append(
                    _unsupported(
                        "", global_field, f"global GitLab {global_field} requires manual migration"
                    )
                )
        if "default" in payload:
            default = payload.get("default")
            if default not in (None, {}):
                issues.append(
                    _unsupported(
                        "",
                        "default",
                        f"GitLab default configuration in {source} requires manual migration",
                    )
                )
        for key, value in payload.items():
            if key in _RESERVED or key.startswith("."):
                if key.startswith(".") and isinstance(value, dict):
                    issues.append(
                        _unsupported(
                            key,
                            "hidden-job",
                            "hidden jobs/extends templates require manual migration",
                        )
                    )
                continue
            if not isinstance(value, dict) or not ("script" in value or "trigger" in value):
                issues.append(
                    _unsupported(
                        "",
                        str(key),
                        f"Unrecognized top-level GitLab semantics in {source} are not ignored by Fleet import",
                    )
                )
                continue
            if key in jobs:
                issues.append(
                    _unsupported(
                        key, "job", "GitLab job-name merging is not reproduced by Fleet import"
                    )
                )
                continue
            jobs[key] = value
    merged["jobs"] = jobs
    return merged


def _unsupported(job: str, field: str, reason: str) -> GitLabImportIssue:
    return GitLabImportIssue(job=job, field=field, reason=reason)
