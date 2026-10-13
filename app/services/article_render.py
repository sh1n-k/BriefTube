from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.services.article_timestamps import parse_timestamp_entries
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
        items = "".join(f"<li>{_render_fact_box_value(item)}</li>" for item in value)
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


_H2_RE = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
ARTICLE_TOC_MIN_HEADINGS = 3


def build_article_toc(body_html: str | None) -> list[dict[str, object]]:
    """Rendered ## headings with their DOM index; empty when too few to need a TOC."""
    items: list[dict[str, object]] = []
    for index, match in enumerate(_H2_RE.finditer(str(body_html or ""))):
        title = html.unescape(_TAG_RE.sub("", match.group(1))).strip()
        if title:
            # Keep the DOM index so the client scrolls to the same h2 even if some are empty.
            items.append({"index": index, "title": title})
    if len(items) < ARTICLE_TOC_MIN_HEADINGS:
        return []
    return items


def build_article_timestamp_links(
    timestamps_text: str | None, video_id: str | None
) -> list[dict[str, str]]:
    safe_video_id = quote(str(video_id or "").strip(), safe="")
    links: list[dict[str, str]] = []
    for entry in parse_timestamp_entries(timestamps_text):
        url = (
            f"https://www.youtube.com/watch?v={safe_video_id}&t={entry.seconds}s"
            if safe_video_id
            else ""
        )
        links.append(
            {"label": entry.label, "seconds": str(entry.seconds), "text": entry.text, "url": url}
        )
    return links
