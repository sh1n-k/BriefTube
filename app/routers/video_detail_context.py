from __future__ import annotations

import hashlib
import json

from fastapi import Request

from app.repositories import downloads as downloads_repo
from app.repositories import videos as videos_repo
from app.routers.template_context import build_template_context
from app.services.article_render import render_fact_box_to_safe_html
from app.services.downloads import is_ffmpeg_available
from app.services.markdown_render import render_markdown_to_safe_html


def _build_video_detail_dynamic_refresh_key(detail: dict[str, object] | None) -> str:
    if not detail:
        return ""
    payload = {
        "pipeline_status": str(detail.get("pipeline_status") or ""),
        "article_title": str(detail.get("article_title") or ""),
        "lead": str(detail.get("lead") or ""),
        "body": str(detail.get("body") or ""),
        "fact_box": str(detail.get("fact_box") or ""),
        "timestamps": str(detail.get("timestamps") or ""),
        "raw_text": str(detail.get("raw_text") or ""),
        "language": str(detail.get("language") or ""),
        "source_type": str(detail.get("source_type") or ""),
        "retry_count": int(str(detail.get("transcript_retry_count") or 0)),
        "llm_provider": str(detail.get("llm_provider") or ""),
        "llm_model": str(detail.get("llm_model") or ""),
        "llm_reasoning_effort": str(detail.get("llm_reasoning_effort") or ""),
        "llm_generated_at": str(detail.get("llm_generated_at") or ""),
        "manual_transcript_job_id": str(detail.get("manual_transcript_job_id") or ""),
        "manual_transcript_status": str(detail.get("manual_transcript_status") or ""),
        "manual_transcript_error": str(detail.get("manual_transcript_error") or ""),
        "manual_transcript_retry_count": int(str(detail.get("manual_transcript_retry_count") or 0)),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()


def _should_auto_refresh_video_detail(detail: dict[str, object] | None) -> bool:
    if not detail:
        return False
    pipeline_status = str(detail.get("pipeline_status") or "").strip()
    manual_transcript_status = str(detail.get("manual_transcript_status") or "").strip()
    return manual_transcript_status in {"pending", "running"} or pipeline_status in {
        "transcript_pending",
        "transcript_processing",
        "llm_pending",
        "llm_processing",
    }


def _build_video_detail_dynamic_context_values(
    detail: dict[str, object] | None,
) -> dict[str, object]:
    article_body_html = ""
    article_lead_html = ""
    article_fact_box_html = ""
    if detail and str(detail.get("article_title") or "").strip():
        article_lead_html = render_markdown_to_safe_html(str(detail.get("lead") or ""))
        article_body_html = render_markdown_to_safe_html(str(detail.get("body") or ""))
        article_fact_box_html = render_fact_box_to_safe_html(str(detail.get("fact_box") or ""))
    return {
        "article_lead_html": article_lead_html,
        "article_body_html": article_body_html,
        "article_fact_box_html": article_fact_box_html,
        "detail_dynamic_refresh_key": _build_video_detail_dynamic_refresh_key(detail),
        "detail_dynamic_auto_refresh": _should_auto_refresh_video_detail(detail),
    }


async def build_video_detail_context(
    request: Request,
    *,
    video_id: str,
    transcript_retry_done: bool = False,
    mark_viewed: bool = False,
) -> dict[str, object]:
    detail = await videos_repo.get_video_detail(request.app.state.runtime.db, video_id)
    if detail and mark_viewed:
        await videos_repo.mark_video_viewed(request.app.state.runtime.db, video_id)
        detail = await videos_repo.get_video_detail(request.app.state.runtime.db, video_id)

    download_defaults = await downloads_repo.get_download_default_settings(
        request.app.state.runtime.db,
        default_output_dir=request.app.state.runtime.config.download_dir,
    )
    return await build_template_context(
        request,
        video=detail,
        transcript_retry_done=transcript_retry_done,
        download_defaults=download_defaults,
        ffmpeg_available=is_ffmpeg_available(),
        **_build_video_detail_dynamic_context_values(detail),
    )


async def build_video_detail_dynamic_context(
    request: Request,
    *,
    video_id: str,
) -> dict[str, object]:
    detail = await videos_repo.get_video_detail(request.app.state.runtime.db, video_id)
    return await build_template_context(
        request,
        video=detail,
        **_build_video_detail_dynamic_context_values(detail),
    )
