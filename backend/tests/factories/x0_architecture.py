"""Current source closure for the existing execution-ownership architecture gate."""

from __future__ import annotations

import ast
import tokenize
from io import StringIO
from pathlib import Path
from shutil import copyfile, copytree, ignore_patterns

from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def replace_python_syntax(source: str, old: str, new: str, *, last: bool = False) -> str:
    """Mutate a structural gate input independently of Python layout and quote style."""

    def replace_span(start_line: int, start_column: int, end_line: int, end_column: int) -> str:
        lines = source.splitlines(keepends=True)
        start = sum(map(len, lines[: start_line - 1])) + start_column
        end = sum(map(len, lines[: end_line - 1])) + end_column
        return source[:start] + new + source[end:]

    try:
        fragment_tree = ast.parse(old.strip())
        source_tree = ast.parse(source)
    except SyntaxError:
        fragment_tree = None
    if fragment_tree is not None and len(fragment_tree.body) == 1:
        pattern = ast.dump(fragment_tree.body[0])
        nodes = [
            node
            for node in ast.walk(source_tree)
            if type(node) is type(fragment_tree.body[0]) and ast.dump(node) == pattern
        ]
        if nodes:
            node = sorted(nodes, key=lambda item: (item.lineno, item.col_offset))[-1 if last else 0]
            return replace_span(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)

    def tokens(text: str) -> list[tuple[object, tokenize.TokenInfo]]:
        result = []
        try:
            for token in tokenize.generate_tokens(StringIO(text).readline):
                if token.type in {
                    tokenize.ENCODING,
                    tokenize.ENDMARKER,
                    tokenize.INDENT,
                    tokenize.DEDENT,
                    tokenize.NEWLINE,
                    tokenize.NL,
                    tokenize.COMMENT,
                }:
                    continue
                value = token.string
                if token.type == tokenize.STRING:
                    try:
                        value = ast.literal_eval(token.string)
                    except (SyntaxError, ValueError):
                        pass  # Python 3.12 tokenizes f-strings as one nonliteral token.
                result.append(((token.type, value), token))
        except (tokenize.TokenError, IndentationError):
            # Mutation fragments may deliberately stop inside an open call.
            pass
        return result

    actual, fragment = tokens(source), [value for value, _ in tokens(old)]
    matches = [
        index
        for index in range(len(actual) - len(fragment) + 1)
        if [value for value, _ in actual[index : index + len(fragment)]] == fragment
    ]
    assert fragment and matches, f"Python mutation target is absent: {old}"
    index = matches[-1] if last else matches[0]
    first, final = actual[index][1], actual[index + len(fragment) - 1][1]
    return replace_span(*first.start, *final.end)


def copy_x0_architecture_sources(destination: Path) -> Path:
    """Copy authored gate inputs, excluding environments and archival enrollment."""
    package = destination / "backend/src/oh_goon"
    copytree(
        ROOT / "backend/src/oh_goon",
        package,
        dirs_exist_ok=True,
        ignore=ignore_patterns("__pycache__", "*.pyc"),
    )
    for relative in (
        "backend/pyproject.toml",
        "docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md",
        "scripts/qualify_b12_development_wsl.sh",
        "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py",
        "backend/tests/native/test_co_r3u2b2b2a_native_refusal.py",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / relative, target)
    return package
