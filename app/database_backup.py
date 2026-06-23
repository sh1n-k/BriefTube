from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path


def backup_sqlite_database_if_present(db_path: str) -> str | None:
    """Create a timestamped local backup before startup migrations mutate SQLite.

    The helper is intentionally best-effort and local-only: missing databases are ignored,
    but copy failures are surfaced so startup does not continue into a risky migration.
    """
    path = Path(db_path).expanduser()
    if not path.exists() or not path.is_file():
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_path = path.with_name(f"{path.name}.backup-{timestamp}")
    shutil.copy2(path, backup_path)
    return str(backup_path)
