from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.i18n import get_texts

REPRESENTATIVE_STATUS_CLASS_EXPECTATIONS = {
    "transcript_pending": "status-badge--transcript-pending",
    "llm_failed": "status-badge--llm-failed",
    "done": "status-badge--done",
}


def _render_status_badge(client: TestClient, status: str) -> str:
    template = client.app.state.templates.get_template("fragments/status_badge.html")
    return template.render(status=status, txt=get_texts("ko"))


def test_status_badge_renders_representative_semantic_classes(client: TestClient) -> None:
    for status, class_name in REPRESENTATIVE_STATUS_CLASS_EXPECTATIONS.items():
        html = _render_status_badge(client, status)
        assert "status-badge" in html
        assert class_name in html


def test_status_badge_unknown_fallback_class(client: TestClient) -> None:
    assert "status-badge--unknown" in _render_status_badge(client, "unknown")


def _extract_css_block(css_text: str, selector: str) -> str:
    start = css_text.index(selector)
    block_start = css_text.index("{", start)
    depth = 0
    for index in range(block_start, len(css_text)):
        char = css_text[index]
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return css_text[block_start + 1 : index]
    raise AssertionError(f"Unclosed CSS block: {selector}")


def _parse_css_vars(css_block: str) -> dict[str, str]:
    pairs = re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", css_block)
    return {name: value.strip() for name, value in pairs}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    normalized = hex_color.strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", normalized):
        raise AssertionError(f"Expected hex color, got: {hex_color}")
    return tuple(int(normalized[i : i + 2], 16) for i in (1, 3, 5))


def _relative_luminance(hex_color: str) -> float:
    rgb = _hex_to_rgb(hex_color)

    def channel(value: int) -> float:
        converted = value / 255
        if converted <= 0.03928:
            return converted / 12.92
        return ((converted + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(component) for component in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(foreground: str, background: str) -> float:
    light = max(_relative_luminance(foreground), _relative_luminance(background))
    dark = min(_relative_luminance(foreground), _relative_luminance(background))
    return (light + 0.05) / (dark + 0.05)


def test_status_badge_color_tokens_keep_readable_contrast() -> None:
    stylesheet = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"
    css_text = stylesheet.read_text(encoding="utf-8")

    selectors = {
        "light": ":root",
        "light_brand": 'html[data-theme="light"][data-tone="brand"]',
        "light_high_contrast": 'html[data-theme="light"][data-tone="high-contrast"]',
        "dark_brand": 'html[data-theme="dark"][data-tone="brand"]',
        "dark_neutral": 'html[data-theme="dark"][data-tone="neutral"]',
        "dark_high_contrast": 'html[data-theme="dark"][data-tone="high-contrast"]',
    }
    vars_by_theme = {
        theme: _parse_css_vars(_extract_css_block(css_text, selector))
        for theme, selector in selectors.items()
    }

    status_pairs = {
        "auto_paused": ("status-no-subtitle-text", "status-no-subtitle-bg"),
        "transcript_pending": ("status-transcript-pending-text", "status-transcript-pending-bg"),
        "transcript_processing": (
            "status-transcript-processing-text",
            "status-transcript-processing-bg",
        ),
        "transcript_done": ("status-done-text", "status-done-bg"),
        "transcript_failed": ("status-transcript-failed-text", "status-transcript-failed-bg"),
        "llm_pending": ("status-llm-pending-text", "status-llm-pending-bg"),
        "llm_processing": ("status-llm-processing-text", "status-llm-processing-bg"),
        "llm_failed": ("status-llm-failed-text", "status-llm-failed-bg"),
        "done": ("status-done-text", "status-done-bg"),
        "manual_review": ("status-manual-review-text", "status-manual-review-bg"),
        "no_subtitle": ("status-no-subtitle-text", "status-no-subtitle-bg"),
        "unknown": ("status-unknown-text", "status-unknown-bg"),
    }

    for theme_name, theme_vars in vars_by_theme.items():
        for status, (text_key, bg_key) in status_pairs.items():
            ratio = _contrast_ratio(theme_vars[text_key], theme_vars[bg_key])
            assert ratio >= 4.5, f"{theme_name}/{status} contrast too low: {ratio:.2f}"
