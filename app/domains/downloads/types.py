from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DownloadActionResult:
    ok: bool
    status_code: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BulkEnqueueResult:
    created_count: int = 0
    duplicate_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
    had_error: bool = False
    error_code: str = ""
    error_message: str = ""
    target_dir: str = ""


@dataclass(slots=True)
class DownloadFileTargetResult:
    ok: bool
    target: Path | None = None
    code: str = ""
    message: str = ""
