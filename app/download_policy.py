from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DownloadOutputDirValidationResult:
    ok: bool
    normalized_path: str
    error_code: str = ""
    error_message: str = ""


def validate_download_output_dir(
    raw_output_dir: str | None,
    *,
    require_absolute: bool = True,
    require_existing: bool = True,
) -> DownloadOutputDirValidationResult:
    raw = str(raw_output_dir or "").strip()
    if not raw:
        return DownloadOutputDirValidationResult(
            ok=False,
            normalized_path="",
            error_code="download_path_empty",
            error_message="download output directory is required",
        )

    path = Path(raw).expanduser()
    if require_absolute and not path.is_absolute():
        return DownloadOutputDirValidationResult(
            ok=False,
            normalized_path=str(path),
            error_code="download_path_must_be_absolute",
            error_message="download output directory must be an absolute path",
        )

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return DownloadOutputDirValidationResult(
            ok=False,
            normalized_path=str(path),
            error_code="download_path_invalid",
            error_message="download output directory path is invalid",
        )

    if require_existing and not resolved.exists():
        return DownloadOutputDirValidationResult(
            ok=False,
            normalized_path=str(resolved),
            error_code="download_path_not_found",
            error_message="download output directory does not exist",
        )

    if resolved.exists() and not resolved.is_dir():
        return DownloadOutputDirValidationResult(
            ok=False,
            normalized_path=str(resolved),
            error_code="download_path_not_directory",
            error_message="download output path must be a directory",
        )

    if resolved.exists() and resolved.is_dir():
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(resolved),
                prefix=".brieftube-write-check-",
                delete=False,
            ) as handle:
                handle.write("ok")
                temp_path = handle.name
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)
        except OSError:
            return DownloadOutputDirValidationResult(
                ok=False,
                normalized_path=str(resolved),
                error_code="download_path_not_writable",
                error_message="download output directory is not writable",
            )

    return DownloadOutputDirValidationResult(
        ok=True,
        normalized_path=str(resolved),
    )
