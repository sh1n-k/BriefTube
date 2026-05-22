from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DownloadRunResult:
    ok: bool
    error_code: str
    error_message: str
    output_path: str | None = None
    file_size_bytes: int | None = None


@dataclass(slots=True)
class DownloadOutputDirValidationResult:
    ok: bool
    normalized_path: str
    error_code: str = ""
    error_message: str = ""


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


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


def _resolve_quality_bound(raw_quality: str | None) -> str:
    normalized = str(raw_quality or "").strip()
    if normalized in {"2160", "1440", "1080", "720", "480"}:
        return normalized
    return "1080"


def _resolve_error_code(stderr_text: str, *, timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    lowered = stderr_text.lower()
    if "ffmpeg" in lowered and "not found" in lowered:
        return "ffmpeg_missing"
    if "ffmpeg" in lowered and "required" in lowered:
        return "ffmpeg_missing"
    if "http error 403" in lowered:
        return "http_403"
    if "http error 404" in lowered:
        return "http_404"
    if "too many requests" in lowered:
        return "http_429"
    if "no module named yt_dlp" in lowered:
        return "yt_dlp_missing"
    return "process_failed"


async def _run_download_command(
    cmd: list[str],
    *,
    timeout_seconds: int,
) -> tuple[int, bytes, bytes]:
    safe_timeout = max(10, int(timeout_seconds))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        logger.warning(
            "event=downloads.subprocess_asyncio_unavailable fallback=threaded_subprocess",
            extra={"event": "downloads.subprocess_asyncio_unavailable"},
        )
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=safe_timeout,
            )
        except FileNotFoundError:
            raise
        except subprocess.TimeoutExpired as exc:
            stdout = bytes(exc.stdout or b"")
            stderr = bytes(exc.stderr or b"")
            return (-9, stdout, stderr)
        return (int(completed.returncode), completed.stdout or b"", completed.stderr or b"")
    except FileNotFoundError:
        raise

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=safe_timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return (-9, b"", b"")
    return (int(proc.returncode or 0), stdout_bytes, stderr_bytes)


async def download_video(
    *,
    video_id: str,
    quality: str,
    overwrite: bool,
    output_dir: str,
    timeout_seconds: int = 1800,
) -> DownloadRunResult:
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return DownloadRunResult(
            ok=False,
            error_code="invalid_video_id",
            error_message="video_id is required",
        )

    quality_bound = _resolve_quality_bound(quality)
    target_dir_validation = validate_download_output_dir(
        output_dir,
        require_absolute=True,
        require_existing=True,
    )
    if not target_dir_validation.ok:
        return DownloadRunResult(
            ok=False,
            error_code=target_dir_validation.error_code or "download_path_invalid",
            error_message=target_dir_validation.error_message
            or "download output directory is unavailable",
        )
    target_dir = Path(target_dir_validation.normalized_path)

    youtube_url = f"https://www.youtube.com/watch?v={normalized_video_id}"
    format_selector = f"bv*[height<={quality_bound}]+ba/b[height<={quality_bound}]/best"
    output_template = str(target_dir / "%(title).120B [%(id)s].%(ext)s")

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-progress",
        "--print",
        "after_move:filepath",
        "--merge-output-format",
        "mp4",
        "-f",
        format_selector,
        "-o",
        output_template,
    ]
    if overwrite:
        cmd.append("--force-overwrites")
    else:
        cmd.append("--no-overwrites")
    cmd.append(youtube_url)

    try:
        returncode, stdout_bytes, stderr_bytes = await _run_download_command(
            cmd,
            timeout_seconds=timeout_seconds,
        )
    except FileNotFoundError:
        return DownloadRunResult(
            ok=False,
            error_code="yt_dlp_missing",
            error_message="yt-dlp module is not installed",
        )

    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

    if returncode == -9:
        return DownloadRunResult(
            ok=False,
            error_code="timeout",
            error_message="download process timed out",
        )

    if returncode != 0:
        error_code = _resolve_error_code(stderr_text)
        message = stderr_text or f"yt-dlp failed with code {returncode}"
        return DownloadRunResult(
            ok=False,
            error_code=error_code,
            error_message=message,
        )

    output_path: Path | None = None
    candidates = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    if candidates:
        output_path = Path(candidates[-1]).resolve()

    if output_path is None or not output_path.exists() or not output_path.is_file():
        matches = sorted(
            target_dir.glob(f"*{normalized_video_id}*"), key=lambda item: item.stat().st_mtime
        )
        if matches:
            output_path = matches[-1].resolve()

    if output_path is None or not output_path.exists() or not output_path.is_file():
        return DownloadRunResult(
            ok=False,
            error_code="output_not_found",
            error_message="download succeeded but output file was not found",
        )

    return DownloadRunResult(
        ok=True,
        error_code="",
        error_message="",
        output_path=output_path.name,
        file_size_bytes=int(output_path.stat().st_size),
    )
