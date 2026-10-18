from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
THEME = ROOT / "frontend" / "src" / "app" / "theme" / "themeTokens.ts"
MIN_RATIO = 4.5


def _section(text: str, name: str) -> dict[str, str]:
    match = re.search(rf"\n  {re.escape(name)}: \{{(.*?)\n  \}},", text, re.DOTALL)
    if match is None:
        raise ValueError(f"theme section {name!r} not found")
    return dict(
        re.findall(r'^\s*(\w+):\s*"(#[0-9a-fA-F]{6})",?$', match.group(1), re.MULTILINE)
    )


def _luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linear(channel: float) -> float:
        return (
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_luminance(foreground), _luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _required_pairs(text: str) -> list[tuple[str, str, str]]:
    surface = _section(text, "surface")
    foreground = _section(text, "text")
    action = _section(text, "action")
    status = _section(text, "status")
    pairs: list[tuple[str, str, str]] = []
    for text_name in ("primary", "secondary", "muted", "link"):
        for surface_name in ("canvas", "panel", "subtle", "elevated"):
            pairs.append(
                (
                    f"text.{text_name} on surface.{surface_name}",
                    foreground[text_name],
                    surface[surface_name],
                )
            )
    pairs.append(
        ("text.inverse on action.primary", foreground["inverse"], action["primary"])
    )
    for text_name in ("primary", "secondary", "muted", "link"):
        pairs.append(
            (
                f"text.{text_name} on action.primarySoft",
                foreground[text_name],
                action["primarySoft"],
            )
        )
    for status_name in ("success", "info", "warning", "danger", "neutral"):
        pairs.append(
            (
                f"status.{status_name} on status.{status_name}Surface",
                status[status_name],
                status[f"{status_name}Surface"],
            )
        )
    return pairs


def main() -> int:
    text = THEME.read_text(encoding="utf-8")
    failures: list[str] = []
    ratios: list[tuple[str, float]] = []
    try:
        pairs = _required_pairs(text)
    except (KeyError, ValueError) as exc:
        print(f"Theme contrast contract failed: {exc}")
        return 1
    for name, foreground, background in pairs:
        ratio = contrast_ratio(foreground, background)
        ratios.append((name, ratio))
        if ratio < MIN_RATIO:
            failures.append(f"{name}: {ratio:.2f}:1 < {MIN_RATIO:.1f}:1")
    if failures:
        print("Theme contrast contract failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    weakest_name, weakest_ratio = min(ratios, key=lambda item: item[1])
    print(
        f"Theme contrast contract passed: {len(ratios)} required pairs >= {MIN_RATIO:.1f}:1; weakest {weakest_name} = {weakest_ratio:.2f}:1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
