from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
TEMPLATE_DIR = ROOT / "app" / "templates"
DIALOG_MODULE = STATIC_DIR / "js" / "ui" / "app-dialog.js"

NATIVE_ALERT = re.compile(r"(?<![\w.])alert\s*\(|window\.alert\b")
NATIVE_CONFIRM_OR_PROMPT = re.compile(
    r"window\.(confirm|prompt)\b|(?<![\w.:])(confirm|prompt)\s*\("
)


def _ui_sources() -> list[Path]:
    files = list(STATIC_DIR.rglob("*.js")) + list(TEMPLATE_DIR.rglob("*.html"))
    return sorted(path for path in files if path.is_file())


def test_service_never_uses_native_alert() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _ui_sources()
        if NATIVE_ALERT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_native_confirm_and_prompt_only_in_app_dialog_safety_net() -> None:
    offenders = []
    for path in _ui_sources():
        if path == DIALOG_MODULE:
            continue
        text = path.read_text(encoding="utf-8")
        for match in NATIVE_CONFIRM_OR_PROMPT.finditer(text):
            line = text[: match.start()].count("\n") + 1
            source_line = text.splitlines()[line - 1]
            # BrieftubeDialog.confirm/prompt calls are the app modal, not the browser dialog.
            if "dialog.confirm" in source_line or "dialog.prompt" in source_line:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert offenders == []


def test_app_dialog_loads_before_other_ui_modules() -> None:
    base = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script defer src="/static/js/([^"]+)"', base)
    assert scripts, "no deferred UI scripts found"
    assert scripts[0] == "ui/app-dialog.js"
    assert "dialog: {" in base
