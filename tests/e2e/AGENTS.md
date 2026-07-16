# E2E rules

- 반드시 `uv run pytest -q -m e2e tests/e2e/<file>.py`로 실행하고 deselected를 통과로 보지 않는다.
- module-scoped fixture나 seed DB를 바꾸면 해당 E2E 파일 전체를 실행한다.
- server 환경 allowlist와 background worker disable을 유지하며 새 worker는 lifecycle parity test에 추가한다.
- 테스트 산출물, screenshot, DB, log, download와 thumbnail은 추적하지 않는다.
