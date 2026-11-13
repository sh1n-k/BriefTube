from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
BASE_TEMPLATE = ROOT / "app" / "templates" / "base.html"

ASSET_REF = re.compile(r'<(?:script|link)\b[^>]*\b(?:src|href)="([^"]+)"')


def _base_asset_refs() -> list[str]:
    return ASSET_REF.findall(BASE_TEMPLATE.read_text(encoding="utf-8"))


def test_base_template_loads_no_external_assets() -> None:
    refs = _base_asset_refs()
    assert refs
    assert [ref for ref in refs if not ref.startswith("/static/")] == []
    for ref in refs:
        assert (STATIC_DIR / ref.removeprefix("/static/")).is_file(), ref


def test_templates_and_css_reference_no_external_assets() -> None:
    external_tag = re.compile(
        r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*[\"']?(?:https?:)?//", re.IGNORECASE
    )
    external_css = re.compile(r"@import|url\(\s*[\"']?(?:https?:)?//", re.IGNORECASE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "app" / "templates").rglob("*.html"))
        if external_tag.search(path.read_text(encoding="utf-8"))
    ] + [
        str(path.relative_to(ROOT))
        for path in sorted((STATIC_DIR / "css").glob("*.css"))
        if external_css.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_tailwind_css_loads_after_app_css() -> None:
    # The former Play CDN injected its <style> after app.css; keep that cascade order.
    refs = _base_asset_refs()
    assert refs.index("/static/css/tailwind.css") > refs.index("/static/css/app.css")
    assert "/static/vendor/htmx-1.9.12.min.js" in refs
    assert "/static/vendor/sortable-1.15.6.min.js" in refs
