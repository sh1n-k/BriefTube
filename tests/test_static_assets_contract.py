from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
BASE_TEMPLATE = ROOT / "app" / "templates" / "base.html"

ASSET_REF = re.compile(r'<(?:script|link)\b[^>]*\b(?:src|href)="([^"]+)"')


def _base_asset_refs() -> list[str]:
    return ASSET_REF.findall(BASE_TEMPLATE.read_text(encoding="utf-8"))


def test_base_template_loads_vendor_scripts_locally() -> None:
    refs = _base_asset_refs()
    assert not [ref for ref in refs if "unpkg.com" in ref or "cdn.jsdelivr.net" in ref]
    for ref in ("/static/vendor/htmx-1.9.12.min.js", "/static/vendor/sortable-1.15.6.min.js"):
        assert ref in refs
        assert (STATIC_DIR / ref.removeprefix("/static/")).is_file()
