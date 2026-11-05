"""Architecture projections of qualified native Make/shell source facts.

All candidates describe authored source; native Make/Bash retains effective
expansion, command binding, process environment and execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from oh_goon.integrations.hashmarks import (
    HashmarksSourceEvidence,
    observe_make_shell_declarations_text,
)


@dataclass(frozen=True, slots=True)
class ShellCommand:
    """One source-word observation, never an effective executable argv.

    ``argv`` retains native literal/conditional words. An unresolved word keeps
    its exact authored expression; ``source_words`` distinguishes it from a
    literal, without inventing a runtime value. Complete evidence remains with
    the enclosing source observation.
    """

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str | None], ...]
    line_number: int
    function: str | None
    source_words: tuple[dict[str, Any], ...]
    source_fact: dict[str, Any]
    binding_observation: dict[str, Any]
    invocation: dict[str, Any] | None = None

    def env(self) -> dict[str, str | None]:
        return dict(self.environment)


@dataclass(frozen=True, slots=True)
class ShellSourceFacts:
    evidence: HashmarksSourceEvidence
    facts: tuple[dict[str, Any], ...]
    commands: tuple[ShellCommand, ...]


@dataclass(frozen=True, slots=True)
class MakeSemanticFacts:
    """Declared targets/variables and conditional source commands, without expansion."""

    evidence: HashmarksSourceEvidence
    facts: tuple[dict[str, Any], ...]
    variables: dict[str, str]
    variable_operators: dict[str, str]
    targets: frozenset[str]
    target_commands: dict[str, tuple[ShellCommand, ...]]


def _source_facts(
    text: str, *, path: str, language: Literal["make", "shell"]
) -> tuple[HashmarksSourceEvidence, tuple[dict[str, Any], ...]]:
    evidence = observe_make_shell_declarations_text(
        text, path=path, language=language, layout="file"
    )
    packet = evidence.packet()
    providers = packet.get("providers") or []
    groups = packet.get("declarations", {}).get("groups") or []
    if (
        packet.get("schema") != "hashmarks.repository-declaration-discovery.v1"
        or not packet.get("observation_identity")
        or len(providers) != 1
        or providers[0].get("state") != "collected"
        or providers[0].get("provenance", {}).get("missing_selected_paths")
        or len(groups) != 1
        or groups[0].get("concept", {}).get("kind") != "make-shell-file-observation"
    ):
        raise ValueError("Architecture Make/shell source observation is unavailable")
    declarations = groups[0].get("declarations") or []
    if len(declarations) != 1:
        raise ValueError("Architecture Make/shell source observation is incomplete")
    value = declarations[0].get("value") or {}
    if (
        value.get("facts_completeness") != "complete-supported-subset"
        or value.get("facts_truncation") != "not-truncated"
        or not isinstance(value.get("facts"), list)
        or value.get("observed_fact_count") != len(value["facts"])
    ):
        raise ValueError("Architecture Make/shell source facts are incomplete")
    return evidence, tuple(value["facts"])


def _authored_words(value: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if isinstance(value.get("authored_words"), list):
        return tuple(value["authored_words"])
    return tuple(
        {"state": "literal-source-candidate", "literal": word} for word in value.get("words", [])
    )


def _word_spelling(word: dict[str, Any]) -> str:
    literal = word.get("literal")
    if isinstance(literal, str):
        return literal
    return str(word.get("source_word", {}).get("expression", ""))


def _literal_environment(value: dict[str, Any]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for exported in value.get("authored_environment_bindings", []):
        words = _authored_words(exported.get("binding", {}))
        result[exported["name"]] = words[0].get("literal") if len(words) == 1 else None
    return result


def _command(
    row: dict[str, Any], observation: dict[str, Any], *, invocation: dict[str, Any] | None = None
) -> ShellCommand:
    value = row["value"]
    words = _authored_words(observation)
    if not words:
        words = tuple(
            {"state": "literal-source-candidate", "literal": word["literal"]}
            if word.get("state") == "literal"
            else {"state": "unresolved-source-expression", "source_word": word}
            for word in value["words"]
        )
    environment = _literal_environment(observation)
    for source in (value, observation):
        for assignment in source.get("command_local_environment", []):
            environment[assignment["name"]] = assignment["value_expression"].get("literal")
    functions = [part for part in value["context"] if part.startswith("function:")]
    return ShellCommand(
        tuple(_word_spelling(word) for word in words),
        tuple(sorted(environment.items())),
        row["start_line"],
        functions[-1].split(":")[1] if functions else None,
        words,
        row,
        observation,
        invocation,
    )


def _commands(
    facts: tuple[dict[str, Any], ...], *, include_function_bodies: bool
) -> tuple[ShellCommand, ...]:
    commands: list[ShellCommand] = []
    for row in facts:
        if row["kind"] != "shell-command-expression":
            continue
        value = row["value"]
        in_function = any(part.startswith("function:") for part in value["context"])
        if include_function_bodies or not in_function:
            commands.append(_command(row, value.get("static_binding_observation", {})))
        for invocation in value.get("invocation_binding_observation", {}).get("candidates", []):
            caller_context = invocation["invocation_site"]["context"]
            if include_function_bodies or not any(
                part.startswith("function:") for part in caller_context
            ):
                commands.append(_command(row, invocation["observation"], invocation=invocation))
    return tuple(commands)


def shell_source_facts(
    text: str, *, path: str = "source.sh", include_function_bodies: bool = True
) -> ShellSourceFacts:
    evidence, facts = _source_facts(text, path=path, language="shell")
    return ShellSourceFacts(
        evidence, facts, _commands(facts, include_function_bodies=include_function_bodies)
    )


def shell_semantic_commands(
    text: str, *, include_function_bodies: bool = True
) -> tuple[ShellCommand, ...]:
    return shell_source_facts(text, include_function_bodies=include_function_bodies).commands


def make_semantic_facts(text: str) -> MakeSemanticFacts:
    evidence, facts = _source_facts(text, path="Makefile", language="make")
    definitions: dict[str, list[dict[str, Any]]] = {}
    targets: set[str] = set()
    for row in facts:
        value = row["value"]
        if row["kind"] == "make-variable-declaration":
            definitions.setdefault(value["name"], []).append(value)
        elif row["kind"] == "make-target-declaration":
            targets.update(value["targets"])
    commands: dict[str, list[ShellCommand]] = {name: [] for name in targets}
    for command in _commands(facts, include_function_bodies=False):
        for name in command.source_fact["value"].get("make_targets", []):
            commands[name].append(command)
    return MakeSemanticFacts(
        evidence,
        facts,
        {name: rows[0]["value_expression"] for name, rows in definitions.items() if len(rows) == 1},
        {
            name: rows[0]["assignment_operator"]
            for name, rows in definitions.items()
            if len(rows) == 1
        },
        frozenset(targets),
        {name: tuple(rows) for name, rows in commands.items()},
    )
