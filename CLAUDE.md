# BriefTube

YouTube 영상의 자막을 수집하고 LLM으로 기사 형태로 재구성하는 로컬 웹 앱.

## Quick Reference

```bash
./scripts/run-dev.sh                    # uvicorn --reload, config.dev.yaml
./scripts/run-prod.sh                   # config.prod.yaml
pip install -e '.[dev]' && pytest -q    # 테스트
python scripts/init_db.py              # DB 초기화 (최초 1회)
```

## Architecture

- 단일 프로세스, `asyncio.create_task` 워커 5개 (poller, download, transcript, llm, notifier)
- 라우터 3분할: `api.py` (/api, JSON) · `views.py` (/views, HTMX fragment) · `pages.py` (/, 전체 페이지)
- 모든 SQL은 `repository.py`에 집중 (Repository 패턴)
- 다운로드 비즈니스 로직은 `domains/downloads/`, yt-dlp 실행은 `services/downloads.py`에 분리
- 프론트엔드: Tailwind CSS (CDN) + htmx 1.9 + `static/js/main-ui.js`, 빌드 파이프라인 없음

### pipeline_status 유효 값

`transcript_pending` → `transcript_processing` → `llm_pending` → `llm_processing` → `done`

분기: `transcript_failed`, `no_subtitle`, `llm_failed`, `manual_review`

## Code Conventions

- Python 3.11+, `from __future__ import annotations` 전 파일 사용
- `@dataclass(slots=True)` for config/state classes
- 라우터에서 런타임 접근: `request.app.state.runtime`
- 템플릿 컨텍스트: 반드시 `template_context.py`의 `build_template_context()`로 생성
- i18n 키 추가 시 `i18n.py`의 `ko`와 `en` dict **양쪽에 반드시 추가**
- i18n 키 네이밍: `섹션_요소_설명` (예: `settings_guard_title`)
- 설정 우선순위: 환경변수 > `APP_CONFIG_FILE` yaml > 코드 기본값
- 새 설정값 추가: `config.py`의 `AppConfig` 필드 + `load_config()`의 yaml/env/clamp 3곳
- 로깅: `event=카테고리.동작` 형식 (예: `event=downloads.job_started`)
- 다운로드 에러 코드: `download_error_registry.py`에서 코드→i18n 키 매핑

## Key Patterns

- **Transcript guard**: 429/403 시 adaptive factor 증가 + hard cooldown. `app_settings` KV 테이블에 영속화
- **HTMX fragment**: `views.py` → `fragments/*.html`, 클라이언트 부분 교체
- **FTS5 동기화**: `schema.sql` 트리거로 자동 인덱싱, 수동 관리 불필요
- **System alerts**: 운영 이벤트를 `system_alerts` 테이블에 기록, `alert_toast.html`로 표시
- **Worker enable/disable**: `app_settings`의 `worker_{name}_enabled` 키로 런타임 제어

## Testing

```bash
pytest -q
```

- **pytest** + `FastAPI.TestClient` (동기 래퍼)
- `conftest.py`: `tmp_path`에 임시 DB/썸네일, 환경변수 monkeypatch

## Known Constraints

- `repository.py`의 `list_videos`에서 f-string SQL (sort column) — 화이트리스트 검증됨, "수정" 불필요
- SQLite 단일 파일 → 동시 쓰기 제한 (WAL 모드로 완화)
