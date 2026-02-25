# BriefTube

BriefTube는 YouTube 콘텐츠를 수집하고, 자막을 기사 형태로 재구성해 로컬에서 확인하는 웹 앱 프로젝트입니다.

## 참고 문서

- [기획서](./01_기획서.md)
- [개발 스펙](./02_개발스펙.md)

## 현재 프로젝트 베이스

- FastAPI + lifespan 기반 단일 프로세스 앱
- `asyncio.create_task` 워커 4종
  - RSS Poller
  - Transcript Fetcher
  - LLM Queue Worker
  - Telegram Notifier
- SQLite + FTS5 + 동기화 트리거
- `/api/*`, `/views/*`, 페이지 라우트 분리
- 공유 `httpx.AsyncClient` 인스턴스 사용
- 설정 화면(`/settings`) 제공
  - 앱 언어 전환(한국어/영어, DB 전역 설정)
  - 채널 일괄 추가(텍스트 + Google Takeout 파일)
  - 채널명/핸들/URL/ID 해석 후 후보 선택 저장

## 최근 반영 사항 (2026-02-25)

- 상단 네비게이션의 설정 메뉴 중복 제거 (`설정` 단일 탭 유지)
- 설정 숫자 입력 필드(`페이지당 영상 수`, `초기 수집 하한선`, `보관 기준`)의 브라우저 기본 증감(위/아래키) 동작 복구
- Transcript Guard / Danger Zone 영역 다국어(i18n) 키 기반으로 통합
- Transcript Guard 상태값(adaptive factor/cooldown/error/success) 조회 및 초기화 경로 유지

## 실행 방법

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python scripts/init_db.py
./scripts/run-dev.sh
```

운영 모드 실행:

```bash
./scripts/run-prod.sh
```

기본 설정 파일:

- 개발: `config.dev.yaml`
- 운영: `config.prod.yaml`

설정 우선순위:

1. 환경변수
2. `APP_CONFIG_FILE`로 지정한 설정 파일
3. 코드 기본값

예시:

```bash
APP_CONFIG_FILE=config.dev.yaml ./scripts/run-dev.sh
APP_CONFIG_FILE=config.prod.yaml OPENCLAW_API_KEY=... ./scripts/run-prod.sh
```

## 테스트

```bash
pip install -e '.[dev]'
pytest -q
```

최소 스모크 테스트:

- `GET /healthz`
- `POST/GET /api/channels` (JSON/form)
- `GET /api/status`

추가 단위테스트:

- 설정 API (`/api/settings`, `/api/settings/language`)
- 비디오 목록 projection (`channel_name`, `thumbnail_url`)
- 채널 일괄 해석/저장 API
- Takeout parser(JSON/CSV)

## 주요 경로

- `app/main.py`: 앱 엔트리 + lifespan
- `app/logging_setup.py`: 로깅 초기화 정책 (콘솔 + 파일 로테이션)
- `app/workers/`: 백그라운드 워커
- `app/services/`: RSS/Transcript/LLM/Telegram 연동
- `app/routers/`: API/HTMX/페이지 라우트
- `sql/schema.sql`: DB 스키마(FTS5 트리거 포함)

## 로그 정책

- 기본: 콘솔 + 파일 동시 기록
- 파일: RotatingFileHandler 사용 (크기 초과 시 로테이션)
- 개발/운영 분리:
  - 개발: `./logs/dev/brieftube-dev.log`
  - 운영: `./logs/prod/brieftube-prod.log`
- 관련 설정 키:
  - `LOG_LEVEL`
  - `LOG_TO_FILE`
  - `LOG_DIR`
  - `LOG_FILE_NAME`
  - `LOG_FILE_MAX_BYTES`
  - `LOG_FILE_BACKUP_COUNT`

## 설정

환경변수(`.env`) 또는 `APP_CONFIG_FILE`(간단 key:value yaml)로 설정 가능.

핵심 키:

- `DB_PATH`
- `THUMBNAIL_DIR`
- `POLLING_INTERVAL_MINUTES`
- `OPENCLAW_API_URL`, `OPENCLAW_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
