"""Bounded TypeScript/JavaScript semantic facts for architecture policy.

This is deliberately not a general parser.  It projects only the authority facts the
repository architecture owns: module imports, browser persistence/network globals,
and static external URL values.  Comments and inert string spellings never become
authority, while simple constant/alias/computed-property forms are resolved.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


@dataclass(frozen=True)
class TypeScriptFacts:
    module_imports: frozenset[str]
    browser_authorities: frozenset[str]
    external_urls: frozenset[str]

    def uses(self, authority: str) -> bool:
        return authority in self.browser_authorities


_BROWSER_ROOTS = {"window", "globalThis", "self"}
_STORAGE_AUTHORITIES = {"localStorage", "sessionStorage", "indexedDB", "IndexedDB"}
_DIRECT_AUTHORITIES = _STORAGE_AUTHORITIES | {"cookieStore"}
_MEMBER_AUTHORITIES = {
    ("document", "cookie"): "document.cookie",
    ("navigator", "sendBeacon"): "navigator.sendBeacon",
}


def _decode_quoted(text: str) -> str:
    quote = text[0]
    body = text[1:-1]
    result: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\" or index + 1 >= len(body):
            result.append(char)
            index += 1
            continue
        nxt = body[index + 1]
        escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
        if nxt in escapes:
            result.append(escapes[nxt])
        elif nxt in {quote, "\\", "/"}:
            result.append(nxt)
        else:
            result.extend(("\\", nxt))
        index += 2
    return "".join(result)


def _tokenize(text: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char in {"'", '"'}:
            start = index
            quote = char
            index += 1
            escaped = False
            while index < length:
                current = text[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    index += 1
                    break
                index += 1
            raw = text[start:index]
            if len(raw) >= 2 and raw[-1] == quote:
                tokens.append(_Token("string", _decode_quoted(raw)))
            continue
        if char == "`":
            start = index + 1
            index += 1
            escaped = False
            interpolated = False
            while index < length:
                current = text[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == "`":
                    value = text[start:index]
                    index += 1
                    if not interpolated:
                        tokens.append(_Token("string", value))
                    break
                elif text.startswith("${", index):
                    interpolated = True
                index += 1
            continue
        if char.isalpha() or char in {"_", "$"}:
            start = index
            index += 1
            while index < length and (text[index].isalnum() or text[index] in {"_", "$"}):
                index += 1
            tokens.append(_Token("ident", text[start:index]))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < length and (text[index].isalnum() or text[index] in {".", "_"}):
                index += 1
            tokens.append(_Token("number", text[start:index]))
            continue
        if text.startswith("=>", index):
            tokens.append(_Token("punct", "=>"))
            index += 2
            continue
        tokens.append(_Token("punct", char))
        index += 1
    return tuple(tokens)


def _statement_end(tokens: tuple[_Token, ...], start: int) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        value = tokens[index].value
        if value in {"(", "[", "{"}:
            depth += 1
        elif value in {")",
            "]",
            "}",
        }:
            depth = max(0, depth - 1)
        elif value == ";" and depth == 0:
            return index
    return len(tokens)


def _string_expr(
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
    constants: dict[str, str],
) -> str | None:
    pieces: list[str] = []
    expect_value = True
    index = start
    while index < end:
        token = tokens[index]
        if expect_value:
            if token.kind == "string":
                pieces.append(token.value)
            elif token.kind == "ident" and token.value in constants:
                pieces.append(constants[token.value])
            elif token.value == "(":
                close = _matching(tokens, index, "(", ")")
                if close is None or close >= end:
                    return None
                nested = _string_expr(tokens, index + 1, close, constants)
                if nested is None:
                    return None
                pieces.append(nested)
                index = close
            else:
                return None
            expect_value = False
        else:
            if token.value != "+":
                return None
            expect_value = True
        index += 1
    if expect_value:
        return None
    return "".join(pieces)


def _matching(
    tokens: tuple[_Token, ...], start: int, opener: str, closer: str
) -> int | None:
    depth = 0
    for index in range(start, len(tokens)):
        value = tokens[index].value
        if value == opener:
            depth += 1
        elif value == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _property_name(
    tokens: tuple[_Token, ...],
    start: int,
    constants: dict[str, str],
) -> tuple[str | None, int]:
    if start >= len(tokens):
        return None, start
    if tokens[start].value == "." and start + 1 < len(tokens):
        token = tokens[start + 1]
        return (token.value, start + 2) if token.kind == "ident" else (None, start + 1)
    if tokens[start].value != "[":
        return None, start
    close = _matching(tokens, start, "[", "]")
    if close is None:
        return None, start
    return _string_expr(tokens, start + 1, close, constants), close + 1


def _module_imports(tokens: tuple[_Token, ...]) -> set[str]:
    modules: set[str] = set()
    for index, token in enumerate(tokens):
        if token.kind != "ident":
            continue
        if token.value in {"import", "require"}:
            if (
                index + 2 < len(tokens)
                and tokens[index + 1].value == "("
                and tokens[index + 2].kind == "string"
            ):
                modules.add(tokens[index + 2].value)
                continue
            if (
                token.value == "import"
                and index + 1 < len(tokens)
                and tokens[index + 1].kind == "string"
            ):
                modules.add(tokens[index + 1].value)
                continue
        if token.value not in {"import", "export"}:
            continue
        end = min(_statement_end(tokens, index), index + 80)
        for cursor in range(index + 1, end - 1):
            if tokens[cursor].value == "from" and tokens[cursor + 1].kind == "string":
                modules.add(tokens[cursor + 1].value)
                break
    return modules


def _declared_names(tokens: tuple[_Token, ...]) -> set[str]:
    declared: set[str] = set()
    for index, token in enumerate(tokens[:-1]):
        if (
            token.value in {"const", "let", "var", "function", "class"}
            and tokens[index + 1].kind == "ident"
        ):
            declared.add(tokens[index + 1].value)
    return declared


def _authority_from_reference(
    tokens: tuple[_Token, ...],
    index: int,
    constants: dict[str, str],
    objects: dict[str, str],
    aliases: dict[str, set[str]],
    declared: set[str],
) -> tuple[set[str], int]:
    token = tokens[index]
    if token.kind != "ident":
        return set(), index + 1
    name = token.value
    if index > 0 and tokens[index - 1].value == "typeof":
        return set(), index + 1
    if name in aliases:
        return set(aliases[name]), index + 1
    if name in _DIRECT_AUTHORITIES:
        return {name}, index + 1
    if name == "fetch" and name not in declared:
        return {"fetch"}, index + 1

    root = objects.get(name, name if name in _BROWSER_ROOTS | {"document", "navigator"} else "")
    if not root:
        return set(), index + 1
    property_name, cursor = _property_name(tokens, index + 1, constants)
    if property_name is None:
        return set(), index + 1
    if root in _BROWSER_ROOTS:
        if property_name in _DIRECT_AUTHORITIES:
            return {property_name}, cursor
        if property_name == "fetch":
            return {"fetch"}, cursor
        if property_name in {"document", "navigator"}:
            second, end = _property_name(tokens, cursor, constants)
            authority = _MEMBER_AUTHORITIES.get((property_name, second or ""))
            return ({authority} if authority else set()), end
    authority = _MEMBER_AUTHORITIES.get((root, property_name))
    return ({authority} if authority else set()), cursor


def project_typescript(text: str) -> TypeScriptFacts:
    tokens = _tokenize(text)
    imports = _module_imports(tokens)
    declared = _declared_names(tokens)
    constants: dict[str, str] = {}
    objects: dict[str, str] = {
        name: name for name in _BROWSER_ROOTS | {"document", "navigator"}
    }
    aliases: dict[str, set[str]] = {}
    authorities: set[str] = set()
    external_urls: set[str] = set()

    # String literals that are actual URL values are semantic data; prose/code examples
    # containing a URL are not treated as network authority.
    for token in tokens:
        if token.kind == "string" and token.value.lower().startswith(
            ("http://", "https://")
        ):
            external_urls.add(token.value)

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.value in {"const", "let", "var"} and index + 2 < len(tokens):
            name_token = tokens[index + 1]
            if name_token.kind == "ident" and tokens[index + 2].value == "=":
                end = _statement_end(tokens, index + 3)
                value = _string_expr(tokens, index + 3, end, constants)
                if value is not None:
                    constants[name_token.value] = value
                    if value.lower().startswith(("http://", "https://")):
                        external_urls.add(value)
                if index + 4 <= end and tokens[index + 3].kind == "ident":
                    source = tokens[index + 3].value
                    if source in objects and index + 4 == end:
                        objects[name_token.value] = objects[source]
                tags, _ = _authority_from_reference(
                    tokens, index + 3, constants, objects, aliases, declared
                )
                if tags:
                    aliases[name_token.value] = tags
                    authorities.update(tags)
                index = max(index + 1, end)
                continue
        tags, cursor = _authority_from_reference(
            tokens, index, constants, objects, aliases, declared
        )
        if tags:
            # An aliased authority definition is enough to establish the forbidden
            # capability. A bare alias use is also semantic, not source spelling.
            authorities.update(tags)
        index = max(index + 1, cursor)

    return TypeScriptFacts(
        module_imports=frozenset(imports),
        browser_authorities=frozenset(authorities),
        external_urls=frozenset(external_urls),
    )
