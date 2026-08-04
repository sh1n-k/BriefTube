from __future__ import annotations

import ast
from pathlib import Path

from app.config import YAML_CONFIG_KEYS
from app.i18n import _TRANSLATIONS, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.pipeline_status import PIPELINE_STATUSES
from app.worker_registry import WORKER_SPECS


def _config_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and ":" in stripped:
            keys.add(stripped.split(":", 1)[0].strip())
    return keys


def test_locale_keys_have_full_parity() -> None:
    locale_keys = {language: set(_TRANSLATIONS[language]) for language in SUPPORTED_LANGUAGES}
    expected = locale_keys[DEFAULT_LANGUAGE]
    assert all(keys == expected for keys in locale_keys.values())


def test_pipeline_statuses_are_unique() -> None:
    assert len(PIPELINE_STATUSES) == len(set(PIPELINE_STATUSES))


def test_project_yaml_keys_match_supported_config_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("config.dev.yaml", "config.prod.yaml"):
        assert _config_keys(root / name) <= YAML_CONFIG_KEYS


def test_repository_package_initializer_has_no_eager_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "app/repositories/__init__.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Import | ast.ImportFrom) for node in ast.walk(tree))


def test_worker_registry_names_and_environment_keys_are_unique() -> None:
    assert len({spec.worker_name for spec in WORKER_SPECS}) == len(WORKER_SPECS)
    assert len({spec.task_name for spec in WORKER_SPECS}) == len(WORKER_SPECS)
    assert len({spec.disable_env_name for spec in WORKER_SPECS}) == len(WORKER_SPECS)


def test_repository_row_helpers_are_not_redefined_outside_common() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "repositories"
    redefined: list[str] = []
    for path in sorted(root.glob("*.py")):
        if path.name in {"_common.py", "__init__.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {
                "row_to_dict",
                "_row_to_dict",
                "rows_to_dicts",
                "_rows_to_dicts",
                "thumbnail_url",
                "_thumbnail_url",
                "with_thumbnail_url",
                "_with_thumbnail_url",
                "normalize_error_message",
                "_normalize_error_message",
            }:
                redefined.append(f"{path.name}:{node.name}")
    assert redefined == []
