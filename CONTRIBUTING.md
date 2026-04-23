# CONTRIBUTING

## 목적

이 문서는 BriefTube 기여 시 충돌/회귀를 줄이기 위한 최소 규칙을 정리합니다.

## 브랜치/워크트리 규칙

- 문서/설정처럼 로직 변경이 없는 작업은 `main`에서 직접 커밋 가능
- 로직 변경은 작업 시작 전에 `main 직접` 또는 `feature + worktree`를 합의
- 작업 중 본인이 만들지 않은 변경은 되돌리지 않음

권장 worktree 예시:

```bash
git worktree add -b feature/<topic> .worktrees/feature-<topic>
```

## 커밋 범위

- 요청 범위 파일만 커밋
- 다음 파일/경로는 커밋 금지
  - `data*.db`
  - `logs/`
  - `thumbnails*/`
  - 로컬 스크린샷/임시 산출물
  - 로컬 운영 설정(`config.dev.yaml` 등)

## 테스트/검증 기준

이 문서가 검증 명령의 canonical source입니다. `README.md`, `AGENTS.md`, `CLAUDE.md`에는 핵심 원칙만 두고, 변경 범위별 최신 명령은 이 섹션을 우선합니다.

- 변경 범위와 맞는 최소 프로파일을 실행
- 부분 실행 시, 미실행 항목과 이유를 PR 설명에 기록
- 가능하면 마지막에 전체 테스트 실행
- 기본 `pytest -q`는 E2E를 제외하며, E2E는 명시 실행(`-m e2e`)으로 수행

자주 쓰는 명령:

```bash
uv run pytest -q tests/test_health.py tests/test_settings_views.py tests/test_video_list.py
uv run pytest -q tests/test_download_api.py tests/test_downloads_page.py tests/test_video_detail.py
uv run pytest -q tests/test_channel_reactivate.py tests/test_channel_list_ui.py tests/test_channel_delete.py tests/test_api_channels.py tests/test_channel_metadata.py tests/test_categories.py
uv run pytest -q -m e2e tests/e2e
uv run pytest -q
```

## 리팩터링 PR 규칙

- 동작 불변을 기본 원칙으로 유지
- 기능 추가와 구조 변경을 같은 PR에 섞지 않음
- 대형 파일 분리는 단계적으로 진행
- 내부 모듈 경계 변경 시 호출부를 새 경계로 함께 갱신하고, 호환 shim을 둘 경우 제거 기준을 PR에 기록
- 저장소 호출은 `app.repositories.<domain>` 모듈을 사용하고, 최상위 `app.repository` 경로를 새로 만들지 않음

## 보안/운영 주의

- 비밀값(`TELEGRAM_BOT_TOKEN`, API 키)은 절대 커밋 금지
- 외부 CLI/모델 응답 원문은 로그에 남기지 않음
