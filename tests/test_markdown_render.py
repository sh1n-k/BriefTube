from __future__ import annotations

from app.services.markdown_render import render_markdown_to_safe_html


def test_render_markdown_to_safe_html_basic() -> None:
    html = render_markdown_to_safe_html("# Title\n\n- item\n\n`code`")
    assert "<h1>Title</h1>" in html
    assert "<li>item</li>" in html
    assert "<code>code</code>" in html


def test_render_markdown_to_safe_html_blocks_script() -> None:
    html = render_markdown_to_safe_html(
        'ok <script>alert("x")</script> [link](https://example.com)'
    )
    assert "<script" not in html
    assert '<a href="https://example.com"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_render_markdown_to_safe_html_returns_empty_for_blank() -> None:
    assert render_markdown_to_safe_html("   ") == ""
