# CONTRIBUTING

BriefTube의 Git 작업과 검증 기준입니다. 검증 명령의 canonical source는 이 문서입니다.

## 변경과 커밋

- 문서·버전 관리되는 기본 설정만 바꾸는 작업은 `main`에서 진행할 수 있습니다.
- 로직·UI·DB 계약 변경은 `feature + worktree`에서 진행합니다.
- 기능 추가와 구조 변경은 가능한 한 분리하고 요청 범위 파일만 커밋합니다.
- 사용자 변경을 되돌리지 않으며 대형 rename/migration과 전면 포맷은 별도 작업으로 진행합니다.
- 다음 경로와 값은 커밋하지 않습니다.
  - `data*.db`, `logs/`, `thumbnails*/`, `downloads/`
  - `config.local.y*ml`, `config.*.local.y*ml`
  - 로컬 screenshot·임시 산출물과 비밀값

`config.dev.yaml`과 `config.prod.yaml`은 버전 관리되는 기본 설정입니다. 개인 override는
`cp config.dev.yaml config.dev.local.yaml`처럼 전체 기본 파일을 ignored 경로에 복사한 뒤 수정하고
`APP_CONFIG_FILE`로 선택합니다. 선택한 YAML은 기본 설정과 merge되지 않습니다.

## 범위별 테스트

변경에 가장 가까운 명령부터 실행하고, 완료 전에는 가능한 한 전체 unit test와 정적 검사를 실행합니다.
실행하지 못한 검증은 이유와 재현 가능한 명령을 남깁니다.

```bash
# 주요 화면과 설정
uv run pytest -q tests/test_health.py tests/test_settings_views.py tests/test_video_list.py

# 다운로드와 영상 상세
uv run pytest -q tests/test_download_api.py tests/test_downloads_page.py tests/test_video_detail.py

# 채널과 카테고리
uv run pytest -q tests/test_channel_reactivate.py tests/test_channel_list_ui.py \
  tests/test_channel_delete.py tests/test_api_channels.py tests/test_channel_metadata.py \
  tests/test_categories.py

# LLM
uv run pytest -q tests/test_llm_client.py tests/test_llm_capabilities.py \
  tests/test_llm_worker_runtime.py tests/test_settings_api.py tests/test_settings_views.py \
  tests/test_logging_policy.py

# LLM 실패 진단 (로컬/prod)
# - 캡처 dir: BRIEFTUBE_LLM_RESPONSE_CAPTURE_DIR (prod 기본 ./logs/prod/llm_raw)
# - 성공 본문 포함: BRIEFTUBE_LLM_RESPONSE_CAPTURE_INCLUDE_CONTENT=1 (기본 off)
# - 캡처 끄기: BRIEFTUBE_LLM_RESPONSE_CAPTURE_DISABLED=1
# 실패 시 로그 필드: stderr_summary, stdout_summary, exit_code
# 실패 시 캡처 jsonl에는 stderr/stdout 스트림이 강제 포함된다 (article body는 INCLUDE_CONTENT 필요)

# transcript와 수동 작업
uv run pytest -q tests/test_transcript_policy.py tests/test_transcript_guard_policy.py \
  tests/test_transcript_guard_concurrency.py tests/test_manual_transcript_worker.py \
  tests/test_manual_article_worker.py

# DB migration과 config
uv run pytest -q tests/test_database_migrations.py \
  tests/test_database_pipeline_status_normalization.py tests/test_config_runtime.py

# retention, 검색과 알림
uv run pytest -q tests/test_retention_page.py tests/test_search_results.py \
  tests/test_alert_toasts.py tests/test_notifier_worker.py

# 아키텍처 계약과 공유 헬퍼
uv run pytest -q tests/test_architecture_contracts.py tests/test_repository_common.py \
  tests/test_worker_wake_sleep.py

# 전체 unit test와 E2E
uv run pytest -q
uv run pytest -q -m e2e tests/e2e
```

pytest에서는 background worker가 기본 비활성화됩니다. worker가 필요한 테스트만
`BRIEFTUBE_ENABLE_<NAME>_WORKER_IN_TESTS=1` 또는 코드에 정의된 전용 alias로 활성화합니다.

## 정적 검사

다음 명령은 모두 통과해야 합니다. 설정은 `pyproject.toml`이 기준입니다.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
```

Shell 변경은 `bash -n run-*.sh scripts/*.sh`로 문법을 확인합니다. HTML/Jinja/JS는 별도
정적 toolchain을 두지 않으므로 관련 unit contract와 `uv run pytest -q -m e2e tests/e2e/<file>.py`를
실행합니다.

`.github/workflows/ci.yml`은 위 정적 검사와 전체 unit/E2E 명령을 그대로 실행합니다. 로컬에서
동일 명령이 통과해야 하며 CI 전용 우회나 별도 test selection을 추가하지 않습니다.

- 새 위반은 수정합니다. 동작 변경이 필요한 자동 수정은 별도 범위로 분리합니다.
- 의도된 예외는 사유와 함께 좁은 `# noqa: <RULE>` 또는 `# pyright: ignore[<rule>]`를 사용합니다.
- 전역 ignore는 동일한 위반이 반복되고 후속 정리 기준이 있을 때만 추가합니다.
- Pyright strict 영역은 `app/config.py`, `app/state.py`, `app/repositories/`, `app/domains/`입니다.

계층 import 방향은 `pyproject.toml`의 import-linter 계약과 동일합니다.

```text
routers : workers → domains → services → repositories → logging_setup → time_utils
  → database/i18n/download_error_registry/download_policy/llm_policy/timezone_policy/config
  → pipeline_status
```

상위 layer만 하위 layer를 import할 수 있습니다. `app.state`, `app.main`, `app.cli`는 composition
root와 entry point이므로 계층 계약 밖에 둡니다.

## 보안과 운영

- Telegram token, API key를 커밋하거나 로그에 기록하지 않습니다.
- transcript 원문, prompt, model 원응답, provider stdout/stderr 전문을 로그에 기록하지 않습니다.
- 운영·공용 DB write와 원격 상태 변경은 명시적 승인 후 수행합니다.
