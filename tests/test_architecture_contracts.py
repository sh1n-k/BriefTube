from __future__ import annotations

import ast
from pathlib import Path

from app.config import YAML_CONFIG_KEYS
from app.i18n import _TRANSLATIONS, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.pipeline_status import PIPELINE_STATUSES
from app.remote_sync_metadata import (
    REMOTE_SYNC_KEY_COLUMNS,
    REMOTE_SYNC_PRUNE_ORDER,
    REMOTE_SYNC_TABLES,
)
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


def test_remote_sync_metadata_covers_every_entity() -> None:
    assert tuple(REMOTE_SYNC_KEY_COLUMNS) == REMOTE_SYNC_TABLES
    assert set(REMOTE_SYNC_PRUNE_ORDER) == set(REMOTE_SYNC_TABLES)


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
