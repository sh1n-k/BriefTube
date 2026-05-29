from __future__ import annotations

from typing import Any

from app.repositories import transcripts as transcripts_repo
from app.services.transcript_guard import (
    TranscriptGuardState,
    _save_guard_state,
    record_transcript_guard_success,
)


async def record_no_subtitle_outcome(
    db: Any,
    guard: TranscriptGuardState,
    *,
    video_id: str,
    adaptive_enabled: bool,
    recovery_success_window: int,
    adaptive_max_factor: float,
    half_open_probe_count: int,
) -> None:
    await transcripts_repo.mark_no_subtitle(db, video_id)
    record_transcript_guard_success(
        guard,
        adaptive_enabled=adaptive_enabled,
        recovery_success_window=recovery_success_window,
        adaptive_max_factor=adaptive_max_factor,
        half_open_probe_count=half_open_probe_count,
    )
    await _save_guard_state(db, guard)
