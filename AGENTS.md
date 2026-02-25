# AGENTS.md

## 프로젝트 요약
- `BriefTube`는 FastAPI + Jinja2 + HTMX 기반 단일 프로세스 앱이다.
- 백그라운드 워커(`rss`, `transcript`, `llm`, `notifier`)가 lifespan에서 함께 기동된다.
- 저장소는 SQLite(`sql/schema.sql`)이며 설정은 DB `app_settings` + `APP_CONFIG_FILE` + 환경변수로 관리된다.

## 빠른 시작/검증
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/init_db.py
./scripts/run-dev.sh
```

전체 테스트:
```bash
.venv/bin/python -m pytest -q
```

빠른 확인(작은 변경 시 우선):
```bash
.venv/bin/python -m pytest -q tests/test_health.py tests/test_settings_api.py tests/test_settings_views.py
```

## 구조 (수정 진입점)
- `app/main.py`: 앱 시작/종료, 워커 등록
- `app/routers/`
  - `api.py`: JSON API
  - `views.py`: HTMX fragment
  - `pages.py`: page render
- `app/templates/`: 화면 템플릿
- `app/repository.py`: DB 접근/설정/정책 로직
- `app/workers/`: RSS/자막/LLM/알림 워커
- `app/services/`: 외부 연동 및 파서
- `tests/`: API/UI/정책 테스트

## 연동 수정 맵 (중요)
- 설정 UI/문구 변경:
  - `app/templates/settings.html`
  - `app/i18n.py`
  - `app/routers/template_context.py`
  - 관련 테스트: `tests/test_settings_api.py`, `tests/test_settings_views.py`
- 영상 목록/시간대/페이지네이션 변경:
  - `app/templates/index.html`, `app/templates/fragments/video_list.html`
  - `app/pagination.py`, `app/time_utils.py`, `app/timezone_policy.py`
  - 관련 테스트: `tests/test_video_pagination_ui.py`, `tests/test_upload_time_timezone_ui.py`
- Transcript Guard/요청 제어 변경:
  - `app/workers/transcript_worker.py`, `app/repository.py`, `app/routers/api.py`, `app/routers/pages.py`
  - 필요 시 `app/config.py`, `.env.example`, `config.example.yaml`, `sql/schema.sql`
  - 관련 테스트: `tests/test_transcript_guard_policy.py`, `tests/test_transcript_guard_settings.py`, `tests/test_transcript_policy.py`
- 채널 일괄 추가/파서 변경:
  - `app/services/takeout_parser.py`, `app/services/bulk_channels.py`, `app/services/channel_resolver.py`
  - `app/routers/api.py`, `app/routers/views.py`
  - 관련 테스트: `tests/test_takeout_parser.py`, `tests/test_bulk_channels_api.py`

## 작업 규칙 (필수)
- 단순 문서/설정 수정은 `main`에서 직접 작업 가능.
- 코드 로직 변경은 시작 전에 사용자와 작업 방식(`main` 직접 vs feature+worktree) 합의.
- 본인이 만들지 않은 변경은 되돌리지 않는다.
- 파괴적 명령(`git reset --hard`, 대량 삭제 등)은 사용자 승인 없이 실행하지 않는다.

## 커밋 전 체크
- 요청 범위 파일만 수정했는지 확인
- 관련 테스트 실행 또는 미실행 사유 기록
- 설정/문서 동기화 필요 시 함께 반영 (`README.md`, `02_개발스펙.md`, `.env.example`, `config.example.yaml`)

## 제외/비밀 정보
- 커밋 금지: `data*.db`, `logs/`, `thumbnails*/`, 로컬 스크린샷
- 비밀값(`OPENCLAW_API_KEY`, `TELEGRAM_BOT_TOKEN` 등) 커밋 금지
