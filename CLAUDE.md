# BriefTube

YouTube 자막 → LLM 기사화 로컬 웹 앱. FastAPI + SQLite + HTMX, 단일 프로세스.

## Quick Reference

```bash
./scripts/run-dev.sh                    # uvicorn --reload, config.dev.yaml
./scripts/run-prod.sh                   # config.prod.yaml
pip install -e '.[dev]' && pytest -q    # 테스트 (E2E 제외)
pytest -q -m e2e tests/e2e             # Playwright E2E
python scripts/init_db.py              # DB 초기화 (최초 1회)
```

## Source Layout (`app/`)

### Core

| 파일 | 역할 |
|------|------|
| `main.py` | FastAPI lifespan, 워커 spawn, 라우터 mount |
| `state.py` | `AppState` dataclass — db, http_client, 서비스 5개, asyncio.Event 5개, notification_queue |
| `config.py` | `AppConfig` dataclass + `load_config()` (yaml → env → clamp) |
| `database.py` | SQLite 초기화(WAL), `_ensure_*_columns()` 점진적 마이그레이션 |
| `repository.py` | 모든 SQL 집중 (~4000줄, 130+ 함수). KV: `get_setting()`/`set_setting()` |
| `repositories/` | 도메인별 re-export 레이어 (증분 리팩터링 중, `repository.py` 위임) |
| `schemas.py` | Pydantic/dataclass 스키마 |
| `i18n.py` | ko/en 번역 dict. 키 네이밍: `섹션_요소_설명` |
| `download_error_registry.py` | 에러 코드 → i18n 키 매핑 |

### Routers (3계층 × 2분할)

| 파일 | 접두사 | 응답 |
|------|--------|------|
| `routers/api.py` | `/api` | JSON |
| `routers/api_downloads.py` | `/api/downloads` | JSON |
| `routers/views.py` | `/views` | HTMX fragment |
| `routers/views_downloads.py` | `/views/downloads` | HTMX fragment |
| `routers/pages.py` | `/` | 전체 HTML |
| `routers/pages_downloads.py` | `/downloads` | 전체 HTML |
| `routers/template_context.py` | — | `build_template_context()` 공통 |

### Workers (7개, `workers/`)

| 워커 | wake event | 역할 |
|------|-----------|------|
| `poller.py` | `poll_now_event` | RSS 피드 폴링 |
| `download_worker.py` | `download_wake_event` | yt-dlp 다운로드 |
| `transcript_worker.py` | — | 자막 수집 (adaptive backoff, lease 잠금) |
| `llm_worker.py` | `llm_wake_event` | LLM 기사화 |
| `manual_article_worker.py` | `manual_article_wake_event` | 수동 기사화 요청 |
| `channel_metadata_worker.py` | `channel_metadata_wake_event` | 채널 메타데이터 크롤링 |
| `notifier_worker.py` | — | Telegram 알림 |

### Services (`services/`)

| 파일 | 역할 |
|------|------|
| `llm.py` | `UnifiedLlmClient` — Codex/Claude CLI, 스키마 검증 |
| `llm_runtime.py` | `LlmRuntimeStatus` 진단 (ready, code, reason, providers) |
| `downloads.py` | yt-dlp 래퍼, ffmpeg 체크 |
| `rss.py` | feedparser 래퍼 |
| `transcript.py` | youtube-transcript-api 래퍼 |
| `transcript_headers.py` | 자막 HTTP 헤더 오버라이드 |
| `channel_resolver.py` | YouTube 채널 정보 크롤링 (BS4) |
| `bulk_channels.py` | 채널 일괄 추가/해석 |
| `takeout_parser.py` | Google Takeout CSV 파싱 |
| `telegram.py` | Telegram 봇 알림 |
| `markdown_render.py` | Markdown 안전 렌더링 (bleach) |
| `channel_handle.py` | 채널 핸들 파싱 (@username) |

### Domains

| 경로 | 역할 |
|------|------|
| `domains/downloads/service.py` | `enqueue_bulk_downloads()`, 환경 검사 |
| `domains/downloads/types.py` | `DownloadActionResult`, `BulkEnqueueResult` |
| `domains/downloads/errors.py` | 다운로드 에러 정의 |

### Frontend

빌드 파이프라인 없음. Tailwind CSS (CDN) + htmx 1.9.

| 파일 | 역할 |
|------|------|
| `static/js/main-ui.js` | HTMX 이벤트, 토스트, 폼, 동적 토글 |
| `static/js/ui/theme.js` | light/dark/system + brand tone (localStorage) |
| `static/js/ui/nav-transition.js` | 페이지 전환 애니메이션 |
| `templates/*.html` | 전체 페이지 8개 (base, index, channels, downloads, queue, settings, retention, video_detail) |
| `templates/fragments/*.html` | HTMX swap 대상 13개 |

## Database (SQLite, WAL)

### 테이블

| 테이블 | 용도 |
|--------|------|
| `categories` | 채널 그룹. `processing_stage`: off / transcript_only / full |
| `channels` | YouTube 채널 + 메타데이터 (handle, thumbnail, description, language_hint) |
| `videos` | `pipeline_status`, `processing_stage_snapshot`, 자막 retry 카운터 |
| `transcripts` | raw_text, language, source_type |
| `articles` | title, lead, body, fact_box, timestamps, llm_provider/model/reasoning_effort |
| `app_settings` | KV 저장소 (워커 on/off, transcript guard, LLM 설정 등). 모든 값 TEXT |
| `system_alerts` | 운영 이벤트 로그 (alert_type, acknowledged_at) |
| `download_jobs` | 다운로드 작업 (status: pending/running/succeeded/failed, error_code) |
| `download_events` | 다운로드 이벤트 로그 |
| `manual_article_jobs` | 수동 기사화 작업 (status: pending/running/succeeded/failed/skipped) |
| `transcripts_fts` | FTS5 가상 테이블 (트리거 자동 동기화) |
| `articles_fts` | FTS5 가상 테이블 (트리거 자동 동기화) |

### pipeline_status 흐름

`transcript_pending` → `transcript_processing` → `transcript_done` → `llm_pending` → `llm_processing` → `done`

분기: `auto_paused` · `transcript_failed` · `no_subtitle` · `llm_failed` · `manual_review`

### app_settings 주요 키

- 워커: `worker_{rss,transcript,llm,notifier,manual_article,channel_metadata}_enabled`
- Transcript guard (8키): `transcript_guard_enabled`, `_factor`, `_hard_cooldown_until`, `_channel_cooldown_*`
- LLM: `llm_provider_primary/fallback`, `llm_model_claude`, `llm_reasoning_effort_{claude,codex}`, `llm_prompt_template`
- 기타: `language`, `timezone`, `retention_days`, `rss_bootstrap_lookback_days`, `download_default_quality`

## Code Conventions

- Python 3.11+, `from __future__ import annotations` 전 파일
- `@dataclass(slots=True)` for config/state classes
- 런타임 접근: `request.app.state.runtime`
- 템플릿 컨텍스트: 반드시 `template_context.py`의 `build_template_context()`로 생성
- i18n 키 추가 시 `i18n.py`의 `ko`와 `en` dict **양쪽에 반드시 추가**
- 설정 우선순위: 환경변수 > `APP_CONFIG_FILE` yaml > 코드 기본값
- 새 설정값: `config.py` AppConfig 필드 + `load_config()` yaml/env/clamp 3곳
- 로깅: `event=카테고리.동작` (예: `event=downloads.job_started`)
- HTMX fragment: 고유 `id` wrapper element 유지 (예: `#channel-list-wrap`)
- HX-Trigger JSON: `json.dumps(..., ensure_ascii=True)` (latin-1 제약)

## Key Patterns

- **Worker wake**: 라우터 `state.{name}_wake_event.set()` → 워커 `.wait()`로 즉시 깨우기
- **Worker enable/disable**: `app_settings`의 `worker_{name}_enabled` 키
- **Transcript guard**: 429/403 시 adaptive factor + hard cooldown. `transcript_guard_*` 키 영속화
- **LLM provider 영속화**: primary/fallback, 모델, reasoning effort 모두 `app_settings`에 저장
- **LLM 스키마 검증**: 자유 텍스트 파싱 금지. 스키마 불일치 → 즉시 실패 → `system_alerts`
- **Category processing_stage**: `off`(미처리) · `transcript_only`(자막만) · `full`(자막+기사). 비디오별 `processing_stage_snapshot`
- **Channel metadata**: 백그라운드 크롤링, 지수 백오프 (rate limit 6h→12h→24h, failure 15m→3h), 최대 12회 재시도
- **FTS5**: `schema.sql` 트리거로 자동 인덱싱, 수동 관리 불필요
- **System alerts**: 운영 이벤트 → `system_alerts` 테이블 → `alert_toast.html`
- **Schema migration**: `database.py`의 `_ensure_*_columns()` 함수로 ALTER TABLE ADD COLUMN. 별도 도구 없음

## Testing

```bash
pytest -q
```

- pytest + `FastAPI.TestClient` (동기 래퍼)
- `conftest.py`: `tmp_path` 임시 DB/썸네일, 환경변수 monkeypatch
- E2E: Playwright (`tests/e2e/`, `-m e2e`로 명시 실행)
- 테스트 프로파일 상세: `AGENTS.md` 참조

## Known Constraints

- `repository.py`의 `list_videos`에서 f-string SQL (sort column) — 화이트리스트 검증됨, "수정" 불필요
- SQLite 단일 파일 → 동시 쓰기 제한 (WAL 모드로 완화)
- `repositories/` 리팩터링 진행 중 — 현재 `repository.py` 위임 구조, 직접 SQL 이동은 미완
