# AGENTS.md

BriefTube는 YouTube RSS 수집, 자막 수집, LLM 기사화, 다운로드/알림을 단일 FastAPI + SQLite 프로세스에서 처리하는 로컬 앱이다. 일반 검증 기준은 `CONTRIBUTING.md`가 canonical source다.

## 바로 쓰는 명령

```bash
uv sync
uv run python scripts/init_db.py
./run-dev.sh                         # http://127.0.0.1:48080
uv run pytest -q                     # E2E 제외
uv run pytest -q -m e2e tests/e2e
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

## 핵심 경로

- `app/main.py`, `app/state.py`, `app/config.py`, `app/database.py`: 앱 조립, 설정, DB 초기화/복구.
- `app/routers/`: `/api` JSON, `/views` HTMX fragment, 페이지 라우트. 템플릿 컨텍스트는 `template_context.py` 사용.
- `app/workers/`: RSS, transcript, LLM, download, manual article/transcript, metadata, notifier 워커.
- `app/services/`: 외부 I/O와 변환 로직. LLM은 `UnifiedLlmClient`.
- `app/repositories/`: DB 접근. 새 저장소 경로는 `app.repositories.<domain>`.
- `app/templates/fragments/`: HTMX swap 계약이 있는 fragment.
- `scripts/`, `run-*`: DB 초기화, 실행, macOS LaunchAgent 관리.

## 작업 방식

- 문서/설정처럼 로직 변경이 없으면 `main`에서 직접 작업 가능.
- 로직 변경은 시작 전에 `main 직접` 또는 `feature + worktree`를 사용자와 합의.
- 본인이 만들지 않은 변경은 되돌리지 않는다.
- `git reset --hard`, 대량 삭제, 전면 포맷팅, 대규모 rename/migration, 바이너리 변경, 대량 의존성 업데이트는 사용자 확인 없이 진행하지 않는다.
- 커밋에는 `data*.db`, `logs/`, `thumbnails*/`, 다운로드 산출물, 로컬 설정, 비밀값을 포함하지 않는다.

## 핵심 불변조건

- `channels.is_active=0`는 삭제가 아니라 “폴링 대상 제외” 의미다.
- RSS Poller는 `rss_channel_not_found(404)`를 만나면 채널을 자동 비활성화한다.
- 수동 재활성화(단건/다건)는 RSS probe 성공 시에만 활성화된다.
- 다건 재활성화는 서버에서 채널별 순차 처리이며 부분 성공을 허용한다.
- 재활성화 결과 토스트는 `HX-Trigger`의 `channel-reactivate-toast`로 전달한다.
- `HX-Trigger` 헤더 JSON은 latin-1 제약 때문에 `json.dumps(..., ensure_ascii=True)`를 유지한다.
- 재활성화 버튼에 `data-save-toast`를 다시 붙이면 중복 토스트가 난다.
- 채널 목록 갱신은 `#channel-list-wrap` fragment 교체 계약을 유지한다.

## 트리거별 확인 포인트

- 채널 관리/재활성화 변경: `app/routers/views.py`, `app/templates/fragments/channel_list.html`, `app/templates/base.html`, `app/i18n.py`와 재활성화 토스트/fragment 계약을 함께 확인.
- 설정/저장 UX 변경: `app/templates/settings.html`, `app/routers/template_context.py`, `tests/test_settings_views.py`를 같이 본다.
- RSS 비활성화 정책 변경: `app/workers/poller.py`, `app/services/rss.py`, `app/repositories/channels.py`를 함께 점검한다.
- LLM 워커 변경: 자유 텍스트 파싱 금지, 스키마 검증 JSON만 저장한다.
- 다운로드 변경: `app/domains/downloads/`, `app/services/downloads.py`, `app/workers/download_worker.py`, `tests/test_download_api.py`, `tests/test_downloads_page.py`를 같이 본다.

## LLM CLI 안전정책

- Codex는 `--output-schema`와 `--output-last-message`, Claude는 `--output-format json`과 `--json-schema`를 우선 사용한다.

- Provider fallback은 동일 스키마와 동일 타임아웃에서만 허용한다.
- 원문 전문과 모델 원응답 전문은 로그에 남기지 않고, `provider`, `exit_code`, `schema_valid`, `retry_count`, `refusal_detected`, `latency_ms` 같은 메타만 남긴다.

## 검증

- transcript 워커는 메인 `state.db` 연결을 공유하고, "워커 1개만 동작" 보장은 프로세스 내 `state.transcript_worker_lock`(asyncio.Lock)으로 한다. 과거 전용 DB 연결 + DB lease(`BEGIN IMMEDIATE`) 방식은 교차 연결 쓰기 경합으로 `database is locked`를 유발해 제거했다.
- pytest 환경에서는 transcript background worker를 기본으로 띄우지 않는다. worker가 필요한 테스트만 `BRIEFTUBE_ENABLE_TRANSCRIPT_WORKER_IN_TESTS=1`로 다시 켠다.
- **정적 검사(ruff + pyright + lint-imports) 통과 필수.** 명령과 위반 처리 정책은 `CONTRIBUTING.md`의 "정적 검사" 섹션을 따른다. 새 모듈 추가 시 계층 import 방향(`routers:workers → domains → services → repositories → infrastructure`) 유지.
- 변경 범위별 최신 명령은 `CONTRIBUTING.md`의 테스트/검증 기준을 canonical source로 따른다.
- 검증을 일부만 했거나 못 했으면 이유와 재현 가능한 command를 남긴다.

### 도구 사용 오류 방지

- `ruff` 개별 실행 대상은 Python 파일만; JS/HTML/Jinja는 e2e/브라우저로 검증한다.
- e2e는 `uv run pytest -q -m e2e ...`; `deselected`는 통과로 보지 않는다.
- DOM `scrollTop`은 표시 후 복원하고, 숨기기 전 저장하며, hidden 상태 값으로 덮어쓰지 않는다.
- 포맷 실패 시 전면 포맷 금지; 본인이 수정한 파일만 포맷 후 재검증한다.
- module-scoped e2e fixture/DB를 바꾸면 뒤 테스트 영향까지 해당 e2e 파일로 확인한다.

## 운영 체크

- 개발 환경에서 재활성화 트러블슈팅은 `tail -f logs/dev/brieftube-dev.log | rg "channels.reactivate"`로 본다.
- LaunchAgent 스크립트는 dry-run 가능: `BRIEFTUBE_LAUNCHD_DRY_RUN=1 ./scripts/install-launchd-prod.sh`.
