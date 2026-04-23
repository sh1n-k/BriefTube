from __future__ import annotations

from youtube_transcript_api._errors import (  # pyright: ignore[reportMissingImports]
    CouldNotRetrieveTranscript,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)

from app.services.transcript_guard import TranscriptErrorCategory, _classify_transcript_error


def test_classify_transcript_error_uses_exception_type_not_message() -> None:
    blocked = RequestBlocked("vid-demo-001")
    assert _classify_transcript_error(blocked) == TranscriptErrorCategory.HARD_THROTTLE


def test_classify_transcript_error_marks_only_real_no_subtitle_cases() -> None:
    disabled = TranscriptsDisabled("vid-demo-002")
    assert _classify_transcript_error(disabled) == TranscriptErrorCategory.NO_SUBTITLE


def test_classify_transcript_error_distinguishes_retryable_and_non_retryable() -> None:
    generic = CouldNotRetrieveTranscript("vid-demo-003")
    unavailable = VideoUnavailable("vid-demo-004")
    assert _classify_transcript_error(generic) == TranscriptErrorCategory.RETRYABLE_TRANSIENT
    assert _classify_transcript_error(unavailable) == TranscriptErrorCategory.NON_RETRYABLE_FAILURE


def test_classify_transcript_error_treats_ip_block_message_as_hard_throttle() -> None:
    class IpBlockedLikeError(CouldNotRetrieveTranscript):
        def __str__(self) -> str:
            return "YouTube is blocking requests from your IP"

    blocked_like = IpBlockedLikeError("vid-demo-005")
    assert _classify_transcript_error(blocked_like) == TranscriptErrorCategory.HARD_THROTTLE
