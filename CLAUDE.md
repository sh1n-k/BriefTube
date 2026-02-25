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

- 단일 프로세스, `asyncio.create_task` 워커 4개 (poller, transcript, llm, notifier)
- `main.py` lifespan → 초기화 → `app.state.runtime` (AppState) 에 저장 → 워커 시작
- 라우터 3분할: `api.py` (/api, JSON) · `views.py` (/views, HTMX fragment) · `pages.py` (/, 전체 페이지)
- 모든 SQL은 `repository.py`에 집중 (Repository 패턴)
- 스키마: `sql/schema.sql` (FTS5 트리거로 transcripts/articles 자동 인덱싱)
- 프론트엔드: Tailwind CSS (CDN) + htmx 1.9, 빌드 파이프라인 없음

## Code Conventions

- Python 3.11+, `from __future__ import annotations` 전 파일 사용
- `@dataclass(slots=True)` for config/state classes
- 라우터에서 런타임 접근: `request.app.state.runtime.db`, `.http_client`, `.config` 등
- 템플릿: 페이지 → `templates/*.html`, HTMX fragment → `templates/fragments/*.html`
- 템플릿 컨텍스트: 반드시 `template_context.py`의 `build_template_context()`로 생성
- i18n 키 추가 시 `i18n.py`의 `ko`와 `en` dict **양쪽에 반드시 추가**
- i18n 키 네이밍: `섹션_요소_설명` (예: `settings_guard_title`)
- 설정 우선순위: 환경변수 > `APP_CONFIG_FILE` yaml > 코드 기본값

## Key Patterns

- **Transcript guard**: `transcript_worker.py`에서 429/403 시 adaptive factor 증가 + hard cooldown, 연속 성공 시 factor 감소. 상태는 `app_settings` KV 테이블에 문자열로 영속화
- **HTMX fragment**: `views.py` 엔드포인트 → `fragments/*.html` 반환, 클라이언트 부분 교체
- **FTS5 동기화**: `schema.sql` AFTER INSERT/UPDATE/DELETE 트리거로 자동 인덱싱, 수동 관리 불필요

## Testing

```bash
pytest -q
```

- **pytest** + `FastAPI.TestClient` (동기 래퍼)
- `conftest.py`: `tmp_path`에 임시 DB/썸네일, 환경변수 monkeypatch

## Known Constraints

- `repository.py`의 `list_videos`에서 f-string SQL (sort column) — 화이트리스트 검증됨, "수정" 불필요
- SQLite 단일 파일 → 동시 쓰기 제한 (WAL 모드로 완화)
