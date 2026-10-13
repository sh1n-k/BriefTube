from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.database import init_database, open_database
from app.repositories import llm as llm_repo
from app.repositories import transcripts as transcripts_repo
from app.services import transcript as transcript_service_module
from app.services.article_render import build_article_toc
from app.services.article_timestamps import (
    ground_timestamps_in_transcript,
    is_grounded_timestamps_text,
    normalize_timestamps_text,
    parse_timestamp_entries,
    strip_position_markers,
)
from app.services.llm import LlmClientError, UnifiedLlmClient
from app.services.llm_invocation import CommandExecutionResult
from app.services.llm_schema import ARTICLE_FIELD_KEYS, build_provider_schema
from app.services.transcript import TranscriptService, build_timed_text

_BODY = (
    "## 배경\n\n원문은 이 사안의 경과와 핵심 주장을 차례로 설명했다. "
    "발표자는 구체적인 수치를 단정하지 않았고, 현장 발언의 맥락만 재배열했다. "
    "불확실한 배경은 기사에 넣지 않았다. 이 문단은 본문 최소 길이를 넘기기 위한 설명을 담는다. "
    "추가 문장으로 길이를 확보한다. 추가 문장으로 길이를 확보한다. 추가 문장으로 길이를 확보한다."
    * 2
)


def _track(code: str, *, generated: bool, dubbed: bool = False) -> SimpleNamespace:
    url = f"https://www.youtube.com/api/timedtext?v=x&lang={code}"
    if generated:
        url += "&kind=asr"
    if dubbed:
        url += "&variant=timing-optimized"
    return SimpleNamespace(language_code=code, is_generated=generated, _url=url)


def _select(tracks: list[SimpleNamespace]) -> SimpleNamespace:
    return TranscriptService()._select_default_track(tracks)


# --- transcript track selection -------------------------------------------------


def test_select_default_track_skips_auto_dubbed_asr_tracks() -> None:
    tracks = [
        _track("ar", generated=True, dubbed=True),
        _track("bn", generated=True, dubbed=True),
        _track("en", generated=True),
        _track("ko", generated=True, dubbed=True),
    ]
    assert _select(tracks).language_code == "en"


def test_select_default_track_prefers_manual_track_in_spoken_language() -> None:
    tracks = [
        _track("zh-Hans", generated=False),
        _track("en-US", generated=False),
        _track("ko", generated=False),
        _track("ko", generated=True),
    ]
    selected = _select(tracks)
    assert selected.language_code == "ko"
    assert selected.is_generated is False


def test_select_default_track_matches_manual_by_primary_language() -> None:
    tracks = [_track("en-US", generated=False), _track("en", generated=True)]
    selected = _select(tracks)
    assert selected.language_code == "en-US"
    assert selected.is_generated is False


def test_select_default_track_uses_original_asr_when_manual_is_translation() -> None:
    tracks = [_track("zh-Hans", generated=False), _track("ko", generated=True)]
    selected = _select(tracks)
    assert selected.language_code == "ko"
    assert selected.is_generated is True


def test_select_default_track_keeps_first_manual_without_original_asr() -> None:
    tracks = [_track("en-US", generated=False), _track("ko", generated=False)]
    assert _select(tracks).language_code == "en-US"


def test_select_default_track_falls_back_to_first_track_when_all_dubbed() -> None:
    tracks = [_track("ar", generated=True, dubbed=True), _track("ko", generated=True, dubbed=True)]
    assert _select(tracks).language_code == "ar"


class _TrackList(list):
    def find_transcript(self, language_codes):
        raise LookupError(f"no transcript for {language_codes}")


def test_select_preferred_track_ignores_auto_dubbed_stored_language() -> None:
    tracks = _TrackList([_track("ar", generated=True, dubbed=True), _track("en", generated=True)])
    assert TranscriptService()._select_preferred_track(tracks, "ar").language_code == "en"


def test_select_preferred_track_matches_case_insensitively_and_prefers_manual() -> None:
    tracks = _TrackList([_track("zh-Hans", generated=True), _track("zh-Hans", generated=False)])
    selected = TranscriptService()._select_preferred_track(tracks, "zh-hans")
    assert selected.is_generated is False


def test_select_preferred_track_keeps_dub_when_no_spoken_track_exists() -> None:
    tracks = _TrackList(
        [_track("ar", generated=True, dubbed=True), _track("bn", generated=True, dubbed=True)]
    )
    assert TranscriptService()._select_preferred_track(tracks, "bn").language_code == "bn"


def test_select_preferred_track_keeps_not_found_error_for_missing_language() -> None:
    tracks = _TrackList([_track("en", generated=True)])
    try:
        TranscriptService()._select_preferred_track(tracks, "ko")
    except LookupError:
        return
    raise AssertionError("missing preferred language must raise")


# --- timed transcript text -----------------------------------------------------


def test_build_timed_text_adds_markers_at_interval() -> None:
    segments = [
        {"text": "first", "start": 0.0},
        {"text": "second", "start": 12.5},
        {"text": "  ", "start": 20.0},
        {"text": "third", "start": 31.2},
        {"text": "fourth", "start": 3725.0},
    ]
    assert build_timed_text(segments) == "[00:00] first\nsecond\n[00:31] third\n[1:02:05] fourth"


def test_build_timed_text_neutralizes_marker_like_caption_text() -> None:
    segments = [{"text": "hi", "start": 0.0}, {"text": "x\n[59:00] fake", "start": 5.0}]
    assert build_timed_text(segments) == "[00:00] hi\nx\nfake"


def test_build_timed_text_returns_none_without_start_times() -> None:
    assert build_timed_text([{"text": "a"}, {"text": "b"}]) is None


def test_fetch_transcript_sync_returns_timed_text(monkeypatch) -> None:
    class _FakeFetched:
        language_code = "en"
        is_generated = True

        def to_raw_data(self):
            return [{"text": "hello", "start": 0.0}, {"text": "world", "start": 45.0}]

    class _FakeTrack:
        language_code = "en"
        is_generated = True
        _url = "https://www.youtube.com/api/timedtext?lang=en&kind=asr"

        def fetch(self):
            return _FakeFetched()

    class _FakeApi:
        def __init__(self, http_client):
            del http_client

        def list(self, video_id):
            del video_id
            return [_FakeTrack()]

    monkeypatch.setattr(transcript_service_module, "YouTubeTranscriptApi", _FakeApi)
    result = TranscriptService()._fetch_transcript_sync("vid-1", None)
    assert result.raw_text == "hello\nworld"
    assert result.timed_text == "[00:00] hello\n[00:45] world"
    assert result.language == "en"
    assert result.source_type == "auto"


# --- article timestamps --------------------------------------------------------


def test_normalize_timestamps_accepts_common_formats() -> None:
    raw = "- [01:05] 도입\n03:10 - 사례\n* 1:02:03 | 결론\n원문에 타임스탬프 없음\n- 2028년: 목표"
    assert normalize_timestamps_text(raw) == "- [01:05] 도입\n- [03:10] 사례\n- [1:02:03] 결론"


def test_normalize_timestamps_returns_empty_for_placeholders() -> None:
    assert normalize_timestamps_text("원문에 시간 표기 없음") == "[]"
    assert normalize_timestamps_text("[]") == "[]"
    assert normalize_timestamps_text("- 이어서: 데모") == "[]"


def test_parse_timestamp_entries_rejects_invalid_clock_values() -> None:
    assert parse_timestamp_entries("- [01:75] 잘못된 초") == []


def test_ground_timestamps_requires_transcript_markers() -> None:
    assert ground_timestamps_in_transcript("- [00:30] 도입", "plain transcript") == "[]"


def test_ground_timestamps_drops_entries_past_last_marker() -> None:
    transcript = "[00:00] a\n[00:30] b\n[01:00] c"
    grounded = ground_timestamps_in_transcript(
        "- [00:30] 도입\n- [01:50] 범위\n- [09:00] 환각", transcript
    )
    assert grounded == "- [00:30] 도입\n- [01:50] 범위"


def test_strip_position_markers_removes_line_start_markers_only() -> None:
    text = "[00:30] 도입\n- [1:02:03] 결론\n개장은 [09:30] 입니다."
    assert strip_position_markers(text) == "도입\n- 결론\n개장은 [09:30] 입니다."


def test_strip_position_markers_keeps_code_slices() -> None:
    text = "print(arr[0:10])\n`a[1:30]`\n```\n[00:10] in code\n```"
    assert strip_position_markers(text) == text


def test_strip_position_markers_tracks_fence_kind_and_indented_code() -> None:
    text = "~~~\n```\n[06:00] still code\n~~~\n    [04:00] indented code\n[00:10] prose"
    assert strip_position_markers(text) == (
        "~~~\n```\n[06:00] still code\n~~~\n    [04:00] indented code\nprose"
    )


def test_is_grounded_timestamps_text_accepts_only_canonical_form() -> None:
    assert is_grounded_timestamps_text("- [00:30] 도입\n- [1:02:03] 결론")
    assert not is_grounded_timestamps_text("- 00:30 도입")
    assert not is_grounded_timestamps_text("- [00:30] 도입\n원문에 타임스탬프 없음")
    assert not is_grounded_timestamps_text("[]")


def test_parse_timestamp_entries_handles_edge_formats() -> None:
    assert parse_timestamp_entries("- [00:30]") == []
    assert parse_timestamp_entries("- [00:30 - 01:10] 범위") == []
    entries = parse_timestamp_entries(
        "1. [00:30] 번호\n2) 01:00 둘째\n- [65:10] 긴 영상\n- [01:05]: 콜론 설명"
    )
    assert [(e.seconds, e.label, e.text) for e in entries] == [
        (30, "00:30", "번호"),
        (60, "01:00", "둘째"),
        (3910, "1:05:10", "긴 영상"),
        (65, "01:05", "콜론 설명"),
    ]


# --- LLM prompt contract -------------------------------------------------------


def test_provider_schema_describes_every_field() -> None:
    schema = build_provider_schema("codex")
    for key in ARTICLE_FIELD_KEYS:
        assert schema["properties"][key]["type"] == "string"
        assert schema["properties"][key]["description"]


def _codex_runner(seen: dict[str, object], article: dict[str, str]):
    async def fake_runner(
        args: list[str], timeout: int, stdin_text: str | None
    ) -> CommandExecutionResult:
        del timeout
        seen["prompt"] = stdin_text
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(article), encoding="utf-8")
        return CommandExecutionResult(exit_code=0, stdout="", stderr="")

    return fake_runner


def _restructure_with_codex(article: dict[str, str], transcript_text: str) -> tuple[dict, str]:
    seen: dict[str, object] = {}
    client = UnifiedLlmClient(
        timeout_seconds=10,
        runner=_codex_runner(seen, article),
        command_exists=lambda _: True,
    )
    result = asyncio.run(
        client.restructure(
            source_title="Source",
            transcript_text=transcript_text,
            settings={"provider_primary": "codex", "prompt_template": "{transcript_text}"},
        )
    )
    return result, str(seen["prompt"])


def test_codex_prompt_carries_article_field_contract() -> None:
    article = {
        "title": "제목",
        "lead": "리드",
        "body": _BODY,
        "fact_box": "- 사실",
        "timestamps": "[]",
    }
    _, prompt = _restructure_with_codex(article, "plain transcript")
    assert "Article field contract:" in prompt
    assert 'return exactly "[]"' in prompt
    assert prompt.rstrip().endswith("fact_box.")


def test_restructure_clears_timestamps_without_transcript_markers() -> None:
    article = {
        "title": "제목",
        "lead": "리드",
        "body": _BODY,
        "fact_box": "- 사실",
        "timestamps": "- [00:10] 지어낸 구간",
    }
    result, _ = _restructure_with_codex(article, "plain transcript")
    assert result["timestamps"] == "[]"


def test_restructure_keeps_grounded_timestamps_and_strips_leaked_markers() -> None:
    article = {
        "title": "제목",
        "lead": "[00:00] 리드",
        "body": _BODY + "\n\n[00:30] 이어지는 문단입니다.",
        "fact_box": "- [00:30] 사실",
        "timestamps": "- [00:00] 도입\n- [00:30] 본론",
    }
    result, _ = _restructure_with_codex(article, "[00:00] hello\n[00:30] world")
    assert result["timestamps"] == "- [00:00] 도입\n- [00:30] 본론"
    assert result["lead"] == "리드"
    assert "[00:30]" not in result["body"]
    assert result["fact_box"] == "- 사실"


def test_restructure_rejects_title_that_is_only_a_position_marker() -> None:
    article = {
        "title": "[00:30]",
        "lead": "리드",
        "body": _BODY,
        "fact_box": "- 사실",
        "timestamps": "[]",
    }
    try:
        _restructure_with_codex(article, "[00:00] hello\n[00:30] world")
    except LlmClientError as exc:
        assert exc.code == "llm_schema_invalid"
        assert exc.retryable
        return
    raise AssertionError("marker-only title must be rejected")


def test_build_article_toc_keeps_dom_index_when_heading_is_empty() -> None:
    body_html = "<h2></h2><h2>One</h2><h2>Two</h2><h2>Three</h2>"
    assert build_article_toc(body_html) == [
        {"index": 1, "title": "One"},
        {"index": 2, "title": "Two"},
        {"index": 3, "title": "Three"},
    ]


# --- persistence ---------------------------------------------------------------


async def _seed_video(db, video_id: str) -> None:
    await db.execute(
        "INSERT INTO channels(channel_id, channel_name, rss_url) VALUES ('UC1', 'C', 'u')"
    )
    await db.execute(
        """
        INSERT INTO videos(video_id, channel_id, title, upload_time, pipeline_status)
        VALUES (?, 'UC1', 'Title', '2026-10-01T00:00:00+00:00', 'transcript_processing')
        """,
        (video_id,),
    )
    await db.commit()


def test_save_transcript_persists_timed_text_and_llm_candidate_reads_it(tmp_path: Path) -> None:
    async def _run():
        db = await open_database(str(tmp_path / "timed.db"))
        try:
            await init_database(db)
            await _seed_video(db, "vid-t")
            await transcripts_repo.save_transcript(
                db,
                video_id="vid-t",
                raw_text="hello",
                timed_text="[00:00] hello",
                language="en",
                source_type="auto",
                thumbnail_path=None,
            )
            candidate = await llm_repo.pop_llm_candidate(db, 3)
            await transcripts_repo.save_transcript(
                db,
                video_id="vid-t",
                raw_text="hello again",
                language="en",
                source_type="manual",
                thumbnail_path=None,
            )
            cursor = await db.execute(
                "SELECT raw_text, timed_text FROM transcripts WHERE video_id = 'vid-t'"
            )
            row = await cursor.fetchone()
            return candidate, tuple(row)
        finally:
            await db.close()

    candidate, row = asyncio.run(_run())
    assert candidate is not None
    assert candidate["timed_text"] == "[00:00] hello"
    assert candidate["raw_text"] == "hello"
    # A refetch without timing replaces stale markers instead of keeping them.
    assert row == ("hello again", None)
