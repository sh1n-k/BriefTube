from __future__ import annotations

import html
import json
from typing import Any, Mapping

from app.services.markdown_render import render_markdown_to_safe_html


def render_fact_box_to_safe_html(fact_box_text: str | None) -> str:
    source = str(fact_box_text or "").strip()
    if not source or source == "{}":
        return ""

    parsed = _load_fact_box_json(source)
    if parsed is None:
        return render_markdown_to_safe_html(source)

    return _render_fact_box_value(parsed)


def _load_fact_box_json(source: str) -> Any | None:
    try:
        return json.loads(source)
    except json.JSONDecodeError:
        return None


def _render_fact_box_value(value: Any) -> str:
    if isinstance(value, Mapping):
        if not value:
            return ""
        items = "".join(
            (
                '<div class="fact-box-entry">'
                f'<dt class="fact-box-label">{html.escape(_humanize_fact_key(str(key)))}</dt>'
                f'<dd class="fact-box-value">{_render_fact_box_value(item)}</dd>'
                "</div>"
            )
            for key, item in value.items()
        )
        return f'<dl class="fact-box-grid">{items}</dl>'

    if isinstance(value, list):
        if not value:
            return ""
        items = "".join(
            f'<li>{_render_fact_box_value(item)}</li>'
            for item in value
        )
        return f'<ul class="fact-box-list">{items}</ul>'

    return _render_fact_box_leaf(value)


def _render_fact_box_leaf(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    rendered = render_markdown_to_safe_html(text)
    if rendered:
        return rendered
    return f'<p class="fact-box-text">{html.escape(text)}</p>'


def _humanize_fact_key(raw_key: str) -> str:
    known_labels = {
        "source_title": "원본 제목",
        "central_claim": "핵심 주장",
        "named_examples": "대표 사례",
        "key_lessons": "핵심 교훈",
        "explicit_uncertainties": "불확실한 점",
    }
    text = raw_key.strip().replace("-", " ").replace("_", " ")
    if not text:
        return "핵심 정보"
    normalized_key = raw_key.strip().lower()
    if normalized_key in known_labels:
        return known_labels[normalized_key]
    if any("\uac00" <= ch <= "\ud7a3" for ch in raw_key):
        return raw_key.strip()
    words = [word for word in text.split() if word]
    if not words:
        return "핵심 정보"
    return " ".join(word.capitalize() for word in words)
