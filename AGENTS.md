# AGENTS.md

## 목적
이 문서는 코드만 읽어서는 놓치기 쉬운 **운영 규칙, 회귀 포인트, 검증 경로**만 담는다.
자명한 프레임워크 설명/디렉터리 나열은 생략한다.

## 작업 방식 (필수)
- 문서/설정처럼 로직 변경이 없는 작업: `main`에서 직접 가능.
- 로직 변경 작업: 시작 전에 사용자와 `main 직접` vs `feature+worktree` 합의.
- 본인이 만들지 않은 변경은 되돌리지 않는다.
- 파괴적 명령(`git reset --hard`, 대량 삭제)은 사용자 승인 없이 금지.

## 핵심 불변조건 (변경 시 반드시 확인)
- `channels.is_active=0`는 “폴링 대상 제외” 의미다.
- RSS Poller는 `rss_channel_not_found(404)`를 만나면 채널을 자동 비활성화한다.
- 수동 재활성화(단건/다건)는 **RSS probe 성공 시에만** 활성화된다.
  - 다건 재활성화는 서버에서 채널별 **순차 처리**한다(동시 fan-out 아님).
  - 다건은 **부분 성공 허용**(성공 채널만 활성화).
- 재활성화 결과 토스트는 `HX-Trigger` 이벤트(`channel-reactivate-toast`)로 전달한다.
  - 헤더는 latin-1 제약이 있으므로 JSON은 `json.dumps(..., ensure_ascii=True)` 형태를 유지한다.
  - 단건/다건 재활성화 버튼에 `data-save-toast`를 다시 붙이면 중복 토스트가 발생한다.
- 채널 목록 갱신은 `#channel-list-wrap` fragment 교체 계약을 유지해야 한다(HTMX swap/OOB 회귀 주의).

## 디버그 로그 규약 (재활성화)
개발 환경(`env: dev`, `log_level: AUTO`)에서는 DEBUG 로그가 켜진다.  
수동 재활성화 트러블슈팅 시 아래 이벤트를 사용한다.

- `channels.reactivate_single_requested`
- `channels.reactivate_probe_start`
- `channels.reactivate_probe_ok`
- `channels.reactivate_probe_failed`
- `channels.reactivate_single_success`
- `channels.reactivate_single_failed`
- `channels.reactivate_bulk_requested`
- `channels.reactivate_bulk_result`
- `channels.reactivate_bulk_failed_ids`
- `channels.reactivate_bulk_delete_done`

로그 확인 예시:
```bash
tail -f logs/dev/brieftube-dev.log | rg "channels.reactivate"
```

## 변경 지점 빠른 맵 (고빈도)
- 채널 관리/재활성화 UX: `app/routers/views.py`, `app/templates/fragments/channel_list.html`, `app/templates/base.html`, `app/i18n.py`
- 설정 페이지/저장 토스트: `app/templates/settings.html`, `app/routers/template_context.py`, `tests/test_settings_views.py`
- RSS 동작/비활성화 정책: `app/workers/poller.py`, `app/services/rss.py`, `app/repository.py`

## 테스트 최소 기준
- 채널 관리/재활성화를 건드렸다면 최소:
```bash
.venv/bin/python -m pytest -q tests/test_channel_reactivate.py tests/test_channel_list_ui.py tests/test_channel_delete.py
```
- 설정/공통 토스트/템플릿 영향이 있으면 추가:
```bash
.venv/bin/python -m pytest -q tests/test_health.py tests/test_settings_api.py tests/test_settings_views.py
```
- 가능하면 전체:
```bash
.venv/bin/python -m pytest -q
```
전체 테스트는 환경 이슈로 간헐적 DB 오픈 오류가 나올 수 있어, 실패 시 단건 재실행으로 재현성 확인 후 보고한다.

## 테스트 프로파일 (LLM 작업용)
- `profile:quick-ui` (템플릿/프런트 공통 변경)
```bash
.venv/bin/python -m pytest -q tests/test_health.py tests/test_settings_views.py tests/test_video_list.py
```
- `profile:downloads` (다운로드 도메인/화면/API/워커 변경)
```bash
.venv/bin/python -m pytest -q tests/test_download_api.py tests/test_downloads_page.py tests/test_video_list.py tests/test_video_detail.py
```
- `profile:channels` (채널 추가/삭제/재활성화/벌크 변경)
```bash
.venv/bin/python -m pytest -q tests/test_channel_reactivate.py tests/test_channel_list_ui.py tests/test_channel_delete.py tests/test_api_channels.py
```
- `profile:full` (릴리즈 전 또는 광범위 리팩터링)
```bash
.venv/bin/python -m pytest -q
```
- 프로파일 일부만 실행한 경우, 실행하지 않은 프로파일과 이유를 함께 기록한다.

## 커밋/보안 체크
- 요청 범위 파일만 커밋하고, 로컬 운영 파일(`config.dev.yaml` 등) 혼입 금지.
- 커밋 금지: `data*.db`, `logs/`, `thumbnails*/`, 로컬 스크린샷.
- 비밀값(`OPENCLAW_API_KEY`, `TELEGRAM_BOT_TOKEN` 등) 커밋 금지.
