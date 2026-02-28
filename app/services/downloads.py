from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys


@dataclass(slots=True)
class DownloadRunResult:
    ok: bool
    error_code: str
    error_message: str
    output_path: str | None = None
    file_size_bytes: int | None = None


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _resolve_quality_bound(raw_quality: str | None) -> str:
    normalized = str(raw_quality or "").strip()
    if normalized in {"1080", "720", "480"}:
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
    target_dir = Path(output_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

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
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return DownloadRunResult(
            ok=False,
            error_code="yt_dlp_missing",
            error_message="yt-dlp module is not installed",
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=max(10, int(timeout_seconds)),
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return DownloadRunResult(
            ok=False,
            error_code="timeout",
            error_message="download process timed out",
        )

    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        error_code = _resolve_error_code(stderr_text)
        message = stderr_text or f"yt-dlp failed with code {proc.returncode}"
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
        matches = sorted(target_dir.glob(f"*{normalized_video_id}*"), key=lambda item: item.stat().st_mtime)
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
