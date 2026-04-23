from __future__ import annotations

from typing import Any


async def seed_channel(
    db: Any,
    *,
    channel_id: str,
    channel_name: str = "Test Channel",
    rss_url: str | None = None,
    is_active: int = 1,
) -> None:
    await db.execute(
        """
        INSERT INTO channels(channel_id, channel_name, rss_url, is_active)
        VALUES (?, ?, ?, ?)
        """,
        (
            channel_id,
            channel_name,
            rss_url or f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            is_active,
        ),
    )
    await db.commit()


async def seed_video(
    db: Any,
    *,
    video_id: str,
    channel_id: str,
    title: str | None = None,
    upload_time: str = "2026-02-26T00:00:00+00:00",
    pipeline_status: str = "transcript_pending",
) -> None:
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (video_id, channel_id, title or video_id, upload_time, pipeline_status),
    )
    await db.commit()
