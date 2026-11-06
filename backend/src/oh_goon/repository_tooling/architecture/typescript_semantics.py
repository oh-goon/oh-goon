"""Existing frontend policy projected from released Hashmarks source facts.

Hashmarks owns syntax, aliases, shadowing and computed property observations.
The authority names below are Oh-Goon's browser/API policy, not another parser.
Uncertain facts remain in the exact producer packet; no tokenizer fallback exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from oh_goon.integrations.hashmarks import (
    HashmarksSourceEvidence,
    observe_source_bindings_text,
)
from oh_goon.repository_tooling.architecture.common import (
    _PYTHON_SOURCE_SNAPSHOT,
    source_binding_packet,
)


@dataclass(frozen=True)
class TypeScriptFacts:
    module_imports: frozenset[str]
    browser_authorities: frozenset[str]
    external_urls: frozenset[str]
    evidence: HashmarksSourceEvidence

    def uses(self, authority: str) -> bool:
        return authority in self.browser_authorities

    def method_parameter_types(self, method: str, *, container: str) -> tuple[str, ...] | None:
        signatures = [
            fact
            for fact in self.evidence.packet()["facts"]
            if fact.get("kind") == "callable-signature"
            and fact.get("name") == method
            and isinstance(fact.get("container"), dict)
            and fact["container"].get("name") == container
        ]
        if len(signatures) != 1:
            return None
        signature = signatures[0]
        if (
            signature.get("qualification") != "qualified"
            or signature.get("conditions")
            or signature.get("parameters_completeness") != "complete"
            or signature.get("parameters_truncation") != "not-truncated"
        ):
            return None
        types: list[str] = []
        for parameter in signature["parameters"]:
            observed = parameter.get("type")
            if (
                parameter.get("optional") is not False
                or parameter.get("rest") is not False
                or not isinstance(observed, dict)
                or observed.get("qualification") != "qualified"
                or observed.get("conditions")
                or observed.get("kind") != "predefined"
                or not isinstance(observed.get("name"), str)
            ):
                return None
            types.append(observed["name"])
        return tuple(types)


_BROWSER_ROOTS = frozenset({"window", "globalThis", "self"})
_DIRECT_AUTHORITIES = frozenset(
    {"fetch", "localStorage", "sessionStorage", "indexedDB", "IndexedDB", "cookieStore"}
)
_MEMBER_AUTHORITIES = {("document", "cookie"), ("navigator", "sendBeacon")}


def project_typescript(text: str, *, path: str = "source.ts") -> TypeScriptFacts:
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    key = (path, text)
    if snapshot is not None and key in snapshot.typescript_facts:
        return snapshot.typescript_facts[key]
    evidence = observe_source_bindings_text(text, path=path)
    packet = source_binding_packet(evidence, language=("typescript", "javascript"))
    if evidence.source_bytes != text.encode("utf-8"):
        raise ValueError("Producer source bytes differ from captured ECMAScript source")
    imports: set[str] = set()
    authorities: set[str] = set()
    external_urls: set[str] = set()
    for fact in packet["facts"]:
        if fact.get("qualification") != "qualified" or fact.get("conditions"):
            continue
        literal = fact.get("literal")
        if fact.get("kind") == "module-declaration" and isinstance(literal, str):
            imports.add(literal)
        if isinstance(literal, str) and literal.lower().startswith(("http://", "https://")):
            external_urls.add(literal)
        target = fact.get("target")
        if not isinstance(target, dict) or target.get("kind") != "lexical-global":
            continue
        root = target.get("name")
        properties = target.get("properties", [])
        if not isinstance(root, str):
            continue
        if root in _BROWSER_ROOTS:
            if not properties:
                continue
            root, properties = properties[0], properties[1:]
        if root in _DIRECT_AUTHORITIES:
            authorities.add(root)
        if properties and (root, properties[0]) in _MEMBER_AUTHORITIES:
            authorities.add(root + "." + properties[0])
    facts = TypeScriptFacts(
        module_imports=frozenset(imports),
        browser_authorities=frozenset(authorities),
        external_urls=frozenset(external_urls),
        evidence=evidence,
    )
    if snapshot is not None:
        snapshot.typescript_facts[key] = facts
    return facts
