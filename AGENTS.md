# AGENTS.md

BriefTube는 YouTube RSS 수집, 자막 수집, Codex 기사화, 다운로드와 알림을 단일
FastAPI + SQLite 프로세스에서 처리하는 로컬 앱이다. Git·검증 규칙의 canonical source는
`CONTRIBUTING.md`다.

## 시작 기준

- 작업 전에 현재 branch와 `git status --short`를 확인하고 사용자 변경을 보존한다.
- branch 선택, 커밋 범위와 검증 명령은 `CONTRIBUTING.md`를 따른다.
- 요청과 직접 관련된 기존 구조를 먼저 수정하며, 새 계층이나 호환 wrapper는 필요한 경우에만 추가한다.
- 대량 삭제, 전면 포맷, 대규모 rename/migration, 의존성 대량 갱신은 별도 확인 없이 수행하지 않는다.

## 탐색 경로

- `app/main.py`, `app/state.py`, `app/config.py`, `app/database.py`: 앱 조립, 설정, DB 초기화·복구
- `app/routers/`: page, `/api` JSON, `/views` HTMX route 구현
- `app/workers/`: RSS, transcript, LLM, download, manual job, metadata, notifier worker
- `app/services/`: 외부 I/O와 변환 로직. LLM entry point는 `UnifiedLlmClient`
- `app/repositories/<domain>.py`: 앱 코드가 사용하는 DB 접근 facade
- `app/templates/fragments/`: HTMX swap 계약이 있는 fragment
- `app/static/js/ui/`: 기능별 브라우저 동작
- `scripts/`, `run-*`: DB 초기화, 실행, macOS LaunchAgent 관리

## 핵심 불변조건

- `channels.is_active=0`은 삭제가 아니라 polling 제외 상태다.
- RSS poller는 `rss_channel_not_found(404)`에서 채널을 자동 비활성화한다.
- 수동 재활성화는 RSS probe 성공 시에만 적용한다. 다건 요청은 순차 처리하며 부분 성공을 허용한다.
- 재활성화 toast는 `HX-Trigger`의 `channel-reactivate-toast`로 전달하고
  `json.dumps(..., ensure_ascii=True)`를 유지한다. 버튼에 `data-save-toast`를 추가하지 않는다.
- 채널 목록 갱신은 `#channel-list-wrap` fragment 교체 계약을 유지한다.
- transcript fetch 경로는 공유 `state.transcript_fetch_gate`를 사용한다. 일반 transcript worker의 단일 실행은
  `state.transcript_worker_lock`으로 보장한다.
- pytest에서는 background worker를 기본 비활성화하고 필요한 테스트만
  `BRIEFTUBE_ENABLE_<NAME>_WORKER_IN_TESTS=1`로 활성화한다.
- LLM은 Codex CLI의 schema-validated JSON만 저장한다. 원문과 provider 원응답 전문을 로그에 남기지 않는다.
- remote sync는 공유 데이터만 Postgres에 mirror하며 로컬 작업 상태와 설정을 공유하지 않는다.
  장애·schema 불일치는 sync만 비활성화하고 로컬 앱 실행을 막지 않는다.
- remote sync 삭제는 tombstone과 LWW 계약을 유지한다. `channels.is_active=0`을 tombstone으로 해석하지 않는다.

## 변경 트리거

- 채널 관리·재활성화: `app/routers/views_channels.py`, `app/routers/views_channel_delete.py`,
  `app/templates/fragments/channel_list.html`, `app/static/js/ui/channel-list-controls.js`
- 채널 추가·import: `app/routers/views_channel_add.py`, `app/routers/views_channel_bulk.py`,
  `app/services/bulk_channels.py`, 관련 channel/category fragment
- 설정 UX: `app/templates/settings.html`, `app/routers/api_settings*.py`,
  `app/routers/template_context.py`, `tests/test_settings_views.py`, `tests/test_settings_api.py`
- RSS 정책: `app/workers/poller.py`, `app/services/rss.py`,
  `app/repositories/channels.py`, `app/repositories/_channels_polling.py`
- LLM: `app/llm_policy.py`, `app/services/llm*.py`, `app/repositories/_settings_llm.py`,
  `app/workers/llm_worker.py`
- 다운로드: `app/domains/downloads/`, `app/services/downloads.py`,
  `app/workers/download_worker.py`, download API/page tests
- remote sync: `app/services/remote_sync.py`, `app/repositories/remote_sync.py`,
  `app/workers/remote_sync_worker.py`, `sql/schema.sql`

## 검증 주의

- 명령과 변경 범위별 기준은 `CONTRIBUTING.md`만 따른다.
- `ruff` 개별 대상에는 Python 파일만 전달한다. HTML/Jinja/JS는 관련 E2E나 브라우저로 검증한다.
- E2E는 반드시 `-m e2e`로 실행하며 `deselected`를 통과로 보지 않는다.
- 포맷 실패 시 본인이 수정한 파일만 포맷한다.
- module-scoped E2E fixture나 DB를 바꾸면 해당 E2E 파일 전체를 실행한다.
- DOM `scrollTop`은 숨기기 전에 저장하고 표시 후 복원한다.

## 운영 안전

- `data*.db`, `logs/`, `thumbnails*/`, `downloads/`, `config.*.local.yaml`, 비밀값을 커밋하지 않는다.
- 운영·공용 DB와 SSH는 기본 읽기 전용으로 다룬다.
- LaunchAgent 변경은 먼저 `BRIEFTUBE_LAUNCHD_DRY_RUN=1 ./scripts/install-launchd-prod.sh`로 확인한다.
