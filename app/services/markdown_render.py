from __future__ import annotations

import html
import logging

import bleach
from markdown_it import MarkdownIt

logger = logging.getLogger(__name__)

_MD = MarkdownIt("commonmark", {"html": False, "linkify": True})

_ALLOWED_TAGS = [
    "p",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "strong",
    "em",
    "a",
    "hr",
]
_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel", "target"],
    "code": ["class"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _set_link_attrs(attrs: dict[tuple[str | None, str], str], _new: bool = False) -> dict[tuple[str | None, str], str]:
    href_key = (None, "href")
    if href_key not in attrs:
        return attrs
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


def render_markdown_to_safe_html(markdown_text: str | None) -> str:
    source = str(markdown_text or "").strip()
    if not source:
        return ""

    try:
        rendered = _MD.render(source)
        cleaned = bleach.clean(
            rendered,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            protocols=_ALLOWED_PROTOCOLS,
            strip=True,
        )
        return bleach.linkify(
            cleaned,
            callbacks=[_set_link_attrs],
            skip_tags=["pre", "code"],
        )
    except Exception as exc:
        logger.warning(
            "event=markdown.render_failed error_type=%s",
            exc.__class__.__name__,
            extra={"event": "markdown.render_failed"},
        )
        return f"<pre>{html.escape(source)}</pre>"

