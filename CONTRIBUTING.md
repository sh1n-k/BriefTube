# CONTRIBUTING

BriefTube 기여 시 충돌과 회귀를 줄이기 위한 기준입니다. 검증 명령의 canonical source는 이 문서입니다.

## 작업 범위

- 문서/설정처럼 로직 변경이 없는 작업은 `main`에서 직접 가능
- 로직 변경은 시작 전에 `main 직접` 또는 `feature + worktree`를 합의
- 작업 중 본인이 만들지 않은 변경은 되돌리지 않음
- 기능 추가와 구조 변경은 가능한 한 분리
- 대형 파일 분리, rename/migration, 전면 포맷팅은 별도 합의 후 단계적으로 진행

## 커밋 범위

- 요청 범위 파일만 커밋
- 다음 파일/경로는 커밋 금지
  - `data*.db`
  - `logs/`
  - `thumbnails*/`
  - 로컬 스크린샷/임시 산출물
  - 로컬 운영 설정(`config.dev.yaml` 등)

## 검증

- 변경 범위와 맞는 최소 명령을 실행하고, 미실행 항목과 이유를 남김
- 기본 `pytest -q`는 E2E 제외. E2E는 `-m e2e`로 명시 실행
- 가능하면 마무리 전에 전체 단위 테스트와 정적 검사를 실행

```bash
# 주요 화면/설정
uv run pytest -q tests/test_health.py tests/test_settings_views.py tests/test_video_list.py

# 다운로드/상세
uv run pytest -q tests/test_download_api.py tests/test_downloads_page.py tests/test_video_detail.py

# 채널/카테고리
uv run pytest -q tests/test_channel_reactivate.py tests/test_channel_list_ui.py tests/test_channel_delete.py tests/test_api_channels.py tests/test_channel_metadata.py tests/test_categories.py

# 전체 단위 테스트 / E2E
uv run pytest -q
uv run pytest -q -m e2e tests/e2e
```

pytest 환경 기본값:

- `tests/conftest.py`가 `TRANSCRIPT_WORKER_LEASE_ENABLED=0`을 설정
- background worker는 기본 비활성. 필요한 테스트만 `BRIEFTUBE_ENABLE_<NAME>_WORKER_IN_TESTS=1` 또는 전용 alias로 활성화

## 정적 검사

PR 머지 전 모두 통과해야 합니다. 설정은 `pyproject.toml`에 있고, 도구는 `uv sync` 시 dev 그룹으로 설치됩니다.

```bash
uv run ruff check .              # lint (E/F/W/I/B/UP/SIM/RUF/ASYNC/S/T20/TID/PTH)
uv run ruff format --check .     # 포매팅 (line-length 100)
uv run pyright                   # 타입 검사 (basic + 핵심 모듈 strict)
uv run lint-imports              # 계층 import 계약
```

빠른 자동 수정:

```bash
uv run ruff check . --fix && uv run ruff format .
```

- **새 위반 발생 시**: 수정이 원칙. 코드 동작을 변경해야 하는 fix는 별도 PR로.
- **의도된 패턴 보호**: 사유를 코멘트로 남기고 인라인 `# noqa: <RULE>` 또는 `# pyright: ignore[<rule>]` 사용. 사유 없는 무시 금지.
- **전역 ignore 추가**: 다수 위반이 동일 패턴일 때만 검토하고 `pyproject.toml`에 사유 코멘트 동반.
- **Pyright strict 영역**: `app/config.py`, `app/state.py`, `app/schemas.py`, `app/repositories/`, `app/domains/`
- **import-linter ignore 추가**: baseline 위반 추적용이므로 후속 정리 계획과 함께 명시

계층 import 방향:

```
routers : workers  →  domains  →  services  →  repositories  →  logging_setup  →  time_utils  →  database/schemas/i18n/pagination/download_error_registry/timezone_policy/config
```

위쪽 layer만 아래쪽 layer를 import할 수 있습니다. `app.state`(composition root), `app.main`, `app.cli`(entry points)는 계약 외부.

## 보안/운영 주의

- 비밀값(`TELEGRAM_BOT_TOKEN`, API 키)은 절대 커밋 금지
- 외부 CLI/모델 응답 원문은 로그에 남기지 않음
- `data*.db`, `logs/`, `thumbnails*/`, 다운로드 산출물은 커밋하지 않음
