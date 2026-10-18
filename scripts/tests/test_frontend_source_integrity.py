from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend" / "src"
GENERATED = ROOT / "generated" / "frontend"
IMPORT_RE = re.compile(
    r"""(?:from\s+|import\s*\()(?P<quote>["'])(?P<module>@/(?:[^"']+)|@generated/(?:[^"']+))(?P=quote)"""
)


def _candidates(module: str) -> tuple[Path, ...]:
    if module.startswith("@/"):
        base = FRONTEND_SRC / module.removeprefix("@/")
    elif module.startswith("@generated/"):
        base = GENERATED / module.removeprefix("@generated/")
    else:  # pragma: no cover - regex owns the admitted aliases
        raise AssertionError(module)
    return (
        base,
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.with_suffix(".d.ts"),
        base / "index.ts",
        base / "index.tsx",
    )


def test_every_frontend_owned_alias_import_resolves_to_packaged_source() -> None:
    unresolved: list[str] = []
    for source in sorted(FRONTEND_SRC.rglob("*")):
        if source.suffix not in {".ts", ".tsx"}:
            continue
        text = source.read_text(encoding="utf-8")
        for match in IMPORT_RE.finditer(text):
            module = match.group("module")
            if not any(candidate.is_file() for candidate in _candidates(module)):
                unresolved.append(f"{source.relative_to(ROOT)} -> {module}")
    assert unresolved == [], "Unresolved frontend-owned imports:\n" + "\n".join(
        unresolved
    )


def test_gitignore_does_not_hide_frontend_feature_directories() -> None:
    feature_names = {
        path.name for path in (FRONTEND_SRC / "features").iterdir() if path.is_dir()
    }
    rules = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    hidden = sorted(name for name in feature_names if f"{name}/" in rules)
    assert hidden == [], (
        f"Unanchored .gitignore rules hide frontend feature source: {hidden}"
    )
