from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DownloadErrorSpec:
    code: str
    message_key: str
    tone: str = "error"
    retryable: bool = False
    status_code: int = 409


_DOWNLOAD_ERROR_SPECS: dict[str, DownloadErrorSpec] = {
    "ffmpeg_missing": DownloadErrorSpec(
        code="ffmpeg_missing",
        message_key="download_toast_ffmpeg_missing",
        retryable=False,
        status_code=409,
    ),
    "download_path_empty": DownloadErrorSpec(
        code="download_path_empty",
        message_key="settings_download_output_dir_error_empty",
        retryable=False,
        status_code=409,
    ),
    "download_path_must_be_absolute": DownloadErrorSpec(
        code="download_path_must_be_absolute",
        message_key="settings_download_output_dir_error_must_be_absolute",
        retryable=False,
        status_code=409,
    ),
    "download_path_invalid": DownloadErrorSpec(
        code="download_path_invalid",
        message_key="settings_download_output_dir_error_invalid",
        retryable=False,
        status_code=409,
    ),
    "download_path_not_found": DownloadErrorSpec(
        code="download_path_not_found",
        message_key="settings_download_output_dir_error_not_found",
        retryable=False,
        status_code=409,
    ),
    "download_path_not_directory": DownloadErrorSpec(
        code="download_path_not_directory",
        message_key="settings_download_output_dir_error_not_directory",
        retryable=False,
        status_code=409,
    ),
    "download_path_not_writable": DownloadErrorSpec(
        code="download_path_not_writable",
        message_key="settings_download_output_dir_error_not_writable",
        retryable=False,
        status_code=409,
    ),
    "download_job_not_found": DownloadErrorSpec(
        code="download_job_not_found",
        message_key="download_toast_output_job_missing",
        retryable=False,
        status_code=404,
    ),
    "download_dir_not_found": DownloadErrorSpec(
        code="download_dir_not_found",
        message_key="download_toast_output_dir_missing",
        retryable=False,
        status_code=404,
    ),
    "download_file_not_found": DownloadErrorSpec(
        code="download_file_not_found",
        message_key="download_toast_output_file_missing",
        retryable=False,
        status_code=404,
    ),
    "not_found": DownloadErrorSpec(
        code="not_found",
        message_key="download_toast_retry_failed",
        retryable=False,
        status_code=404,
    ),
    "invalid_status": DownloadErrorSpec(
        code="invalid_status",
        message_key="download_toast_retry_failed",
        retryable=True,
        status_code=409,
    ),
    "already_changed": DownloadErrorSpec(
        code="already_changed",
        message_key="download_toast_retry_failed",
        retryable=True,
        status_code=409,
    ),
    "unknown": DownloadErrorSpec(
        code="unknown",
        message_key="download_toast_request_failed",
        retryable=True,
        status_code=500,
    ),
}


def get_download_error_spec(code: str | None) -> DownloadErrorSpec:
    normalized = str(code or "").strip().lower() or "unknown"
    return _DOWNLOAD_ERROR_SPECS.get(normalized, _DOWNLOAD_ERROR_SPECS["unknown"])


def build_download_error_payload(
    *,
    code: str | None,
    message: str,
    ok: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    spec = get_download_error_spec(code)
    payload: dict[str, Any] = {
        "ok": bool(ok),
        "code": spec.code,
        "message": message,
        "message_key": spec.message_key,
        "tone": spec.tone,
        "retryable": spec.retryable,
    }
    payload.update(extra)
    return payload
