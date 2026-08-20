from __future__ import annotations

import json

import pytest

from app.services.llm_errors import LlmClientError
from app.services.llm_payload import (
    ARTICLE_BODY_MIN_CHARS,
    article_body_format_invalid,
    article_body_too_short,
    article_looks_like_process_status,
    coerce_article,
    looks_like_over_escaped_markdown,
    normalize_article_text,
    normalize_fact_box_text,
    parse_provider_output,
)

_VALID_BODY_TAIL = (
    "원문은 이 사안의 경과와 핵심 주장을 차례로 설명했다. "
    "발표자는 구체적인 수치를 단정하지 않았고, 현장 발언의 맥락만 재배열했다. "
    "불확실한 배경은 기사에 넣지 않았다. "
    "이어진 대목에서도 원문에 없는 해석을 보태지 않고 발언 순서를 유지했다."
) * 2


def _valid_body(prefix: str) -> str:
    return f"{prefix.rstrip()}\n\n{_VALID_BODY_TAIL}"


def test_normalize_article_text_unescapes_over_escaped_newlines() -> None:
    raw = "## 제목\\n\\n첫 문단.\\n\\n## 다음\\n\\n둘째 문단."
    assert looks_like_over_escaped_markdown(raw) is True
    fixed = normalize_article_text(raw)
    assert "\\n" not in fixed
    assert fixed.splitlines()[0] == "## 제목"
    assert "## 다음" in fixed.splitlines()
    assert article_body_format_invalid(fixed) is False


def test_normalize_article_text_unescapes_single_escaped_newline_before_heading() -> None:
    raw = "서론 문단입니다.\\n## 다음 제목\\n본문이 이어집니다."
    assert looks_like_over_escaped_markdown(raw) is True
    fixed = normalize_article_text(raw)
    assert fixed.splitlines()[1] == "## 다음 제목"


def test_normalize_article_text_preserves_normal_markdown() -> None:
    raw = "## 제목\n\n첫 문단.\n\n## 다음\n\n둘째 문단."
    assert looks_like_over_escaped_markdown(raw) is False
    assert normalize_article_text(raw) == raw.strip()


def test_normalize_article_text_ignores_windows_paths() -> None:
    raw = r"참고 경로 C:\notes 와 D:\notes 를 확인하세요. 추가 설명입니다."
    assert looks_like_over_escaped_markdown(raw) is False
    assert normalize_article_text(raw) == raw


def test_normalize_fact_box_preserves_valid_json_with_escaped_newlines() -> None:
    fb = json.dumps(
        {"central_claim": "첫번째 줄\n두번째 줄", "note": "a\nb"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert json.loads(fb)["central_claim"] == "첫번째 줄\n두번째 줄"
    assert normalize_fact_box_text(fb) == fb
    article = coerce_article(
        {
            "title": "제목",
            "lead": "리드",
            "body": _valid_body("## A\n\n본문\n\n## B\n\n더 본문"),
            "fact_box": fb,
            "timestamps": "[]",
        },
        provider="codex",
    )
    assert article["fact_box"] == fb
    assert json.loads(article["fact_box"])["note"] == "a\nb"


def test_normalize_fact_box_unescapes_markdown_fact_box() -> None:
    raw = "### 핵심\\n\\n- 항목1\\n\\n- 항목2"
    fixed = normalize_fact_box_text(raw)
    assert fixed.startswith("### 핵심\n")
    assert "\\n" not in fixed


def test_coerce_article_normalizes_body_and_markdown_fact_box() -> None:
    article = coerce_article(
        {
            "title": "제목",
            "lead": "리드",
            "body": "## A\\n\\n본문\\n\\n## B\\n\\n더 본문\\n\\n" + _VALID_BODY_TAIL,
            "fact_box": "### 핵심\\n\\n- 항목1\\n\\n- 항목2",
            "timestamps": "[]",
        },
        provider="codex",
    )
    assert "\\n" not in article["body"]
    assert article["body"].startswith("## A\n")
    assert "\\n" not in article["fact_box"]
    assert article["fact_box"].startswith("### 핵심\n")


def test_coerce_article_rejects_body_that_stays_invalid() -> None:
    # No literal \\n to unescape; multiple ## mid-line so markdown headings never form.
    collapsed = ("본문내용 " * 50) + "## 중간제목 " + ("본문내용 " * 20) + "## 또다른 "
    assert article_body_format_invalid(collapsed) is True
    with pytest.raises(LlmClientError) as exc_info:
        coerce_article(
            {
                "title": "제목",
                "lead": "리드",
                "body": collapsed,
                "fact_box": "{}",
                "timestamps": "[]",
            },
            provider="codex",
        )
    assert exc_info.value.code == "llm_schema_invalid"
    assert exc_info.value.retryable is True
    assert "body format" in str(exc_info.value)


def test_coerce_article_allows_prose_mentioning_hash_hash() -> None:
    body = (
        "이 글에서는 마크다운 헤딩 문법 ## 을 설명합니다. "
        "예시는 인라인 코드가 아니라 일반 문장으로만 언급합니다. " + ("추가 설명 " * 30)
    )
    article = coerce_article(
        {
            "title": "제목",
            "lead": "리드",
            "body": body,
            "fact_box": "{}",
            "timestamps": "[]",
        },
        provider="codex",
    )
    assert article["body"] == body.strip()


def test_parse_provider_output_accepts_escaped_body_json() -> None:
    # Python "\\n" is backslash+n; json round-trip keeps that over-escaped form.
    payload = {
        "title": "다들 열정",
        "lead": "리드 문장",
        "body": "## 열정은 자동\\n\\n본문 문단.\\n\\n## 성공과 결과\\n\\n다른 문단.\\n\\n"
        + _VALID_BODY_TAIL,
        "fact_box": "### 핵심\\n\\n- a\\n\\n- b",
        "timestamps": "[]",
    }
    article = parse_provider_output("codex", json.dumps(payload, ensure_ascii=False))
    assert article["body"].startswith("## 열정은 자동\n")
    assert "\\n" not in article["body"]
    assert article_body_format_invalid(article["body"]) is False


def test_parse_provider_output_preserves_valid_real_newlines() -> None:
    payload = {
        "title": "제목",
        "lead": "리드",
        "body": _valid_body("## 제목\n\n정상 본문입니다.\n\n## 다음\n\n계속"),
        "fact_box": "{}",
        "timestamps": "[]",
    }
    article = parse_provider_output("codex", json.dumps(payload, ensure_ascii=False))
    assert article["body"] == payload["body"]


def test_article_body_too_short_matches_min_chars() -> None:
    assert article_body_too_short("짧음") is True
    assert article_body_too_short("가" * (ARTICLE_BODY_MIN_CHARS - 1)) is True
    assert article_body_too_short("가" * ARTICLE_BODY_MIN_CHARS) is False


def test_coerce_article_rejects_short_process_status_body() -> None:
    with pytest.raises(LlmClientError) as exc_info:
        coerce_article(
            {
                "title": "Reading the full prompt and transcript",
                "lead": "The article request was offloaded; I will load the complete source before writing.",
                "body": "I need the full transcript and schema from the offloaded prompt file.",
                "fact_box": "Source: prompt_0.txt",
                "timestamps": "[]",
            },
            provider="grok",
        )
    assert exc_info.value.code == "llm_schema_invalid"
    assert exc_info.value.retryable is True
    assert "too short" in str(exc_info.value)


def test_coerce_article_rejects_process_status_even_when_body_is_long() -> None:
    body = _valid_body("원문을 확인한 뒤 기사로 재구성한다.")
    assert article_body_too_short(body) is False
    assert (
        article_looks_like_process_status(
            title="요청 확인 중",
            lead="전체 요청 파일을 읽어 원문을 모두 확인한다.",
            fact_box="{}",
        )
        is True
    )
    with pytest.raises(LlmClientError) as exc_info:
        coerce_article(
            {
                "title": "요청 확인 중",
                "lead": "전체 요청 파일을 읽어 원문을 모두 확인한다.",
                "body": body,
                "fact_box": "{}",
                "timestamps": "[]",
            },
            provider="grok",
        )
    assert exc_info.value.code == "llm_schema_invalid"
    assert "process-status" in str(exc_info.value)


def test_coerce_article_allows_search_in_real_article_title() -> None:
    body = _valid_body(
        "## 배경\n\n카보베르데라는 검색어와 함께 골키퍼의 정체가 다시 관심을 끌고 있다."
    )
    article = coerce_article(
        {
            "title": "카보베르데 검색하셨죠? 팔로워가 늘어난 골키퍼",
            "lead": "검색어와 함께 골키퍼의 정체가 다시 관심을 끌고 있다.",
            "body": body,
            "fact_box": "{}",
            "timestamps": "[]",
        },
        provider="grok",
    )
    assert "검색" in article["title"]
    assert article["body"] == body
