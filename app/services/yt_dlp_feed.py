from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

YtDlpCommandRunner = Callable[[list[str], float], Awaitable[tuple[int, str, str]]]


class YtDlpFeedError(RuntimeError):
    pass


class YtDlpFeedService:
    def __init__(
        self,
        *,
        playlist_limit: int = 15,
        timeout_seconds: int = 60,
        longform_min_seconds: int = 180,
        runner: YtDlpCommandRunner | None = None,
    ) -> None:
        self.playlist_limit = max(1, min(50, int(playlist_limit)))
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.longform_min_seconds = max(0, int(longform_min_seconds))
        self._runner = runner or _run_yt_dlp_command

    async def fetch_channel_feed(self, channel_id: str) -> list[dict[str, str]]:
        normalized_channel_id = str(channel_id or "").strip()
        if not normalized_channel_id:
            return []

        channel_url = f"https://www.youtube.com/channel/{normalized_channel_id}/videos"
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-errors",
            "--no-warnings",
            "--quiet",
            "--skip-download",
            "--dump-json",
            "--socket-timeout",
            str(self.timeout_seconds),
            "--playlist-end",
            str(self.playlist_limit),
            channel_url,
        ]
        return_code, stdout, stderr = await self._runner(command, float(self.timeout_seconds))
        if not stdout.strip() and (return_code != 0 or stderr.strip()):
            raise YtDlpFeedError((stderr or f"yt-dlp exited with code {return_code}").strip())

        entries = _parse_yt_dlp_json_lines(
            stdout,
            longform_min_seconds=self.longform_min_seconds,
        )
        logger.info(
            "event=yt_dlp.feed_fetched channel_id=%s entries=%d playlist_limit=%d longform_min_seconds=%d",
            normalized_channel_id,
            len(entries),
            self.playlist_limit,
            self.longform_min_seconds,
            extra={"event": "yt_dlp.feed_fetched", "worker": "rss"},
        )
        return entries


async def _run_yt_dlp_command(command: list[str], timeout_seconds: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise YtDlpFeedError(f"yt-dlp timed out after {timeout_seconds:.0f}s") from exc
    return (
        int(proc.returncode or 0),
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


def _parse_yt_dlp_json_lines(
    stdout: str,
    *,
    longform_min_seconds: int,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        entry = _normalize_yt_dlp_entry(payload, longform_min_seconds=longform_min_seconds)
        if entry is not None:
            entries.append(entry)
    return entries


def _normalize_yt_dlp_entry(
    payload: dict[str, Any],
    *,
    longform_min_seconds: int,
) -> dict[str, str] | None:
    video_id = str(payload.get("id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not video_id or not title:
        return None

    webpage_url = str(payload.get("webpage_url") or payload.get("url") or "").strip()
    if "/shorts/" in webpage_url:
        return None

    duration = _parse_duration_seconds(payload.get("duration"))
    if duration is None or duration < longform_min_seconds:
        return None

    published = _normalize_published_time(payload)
    if not published:
        return None

    return {
        "video_id": video_id,
        "title": title,
        "published": published,
        "thumbnail_url": _pick_thumbnail_url(payload),
    }


def _parse_duration_seconds(value: object) -> int | None:
    if isinstance(value, int | float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value.strip())))
        except ValueError:
            return None
    return None


def _normalize_published_time(payload: dict[str, Any]) -> str:
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, int | float):
        return datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()

    upload_date = str(payload.get("upload_date") or "").strip()
    if len(upload_date) == 8 and upload_date.isdigit():
        parsed = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
        return parsed.isoformat()
    return ""


def _pick_thumbnail_url(payload: dict[str, Any]) -> str:
    thumbnail = str(payload.get("thumbnail") or "").strip()
    if thumbnail:
        return thumbnail

    thumbnails = payload.get("thumbnails")
    if not isinstance(thumbnails, list):
        return ""
    for item in reversed(thumbnails):
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            if url:
                return url
    return ""
