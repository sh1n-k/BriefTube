# BriefTube

YouTube 채널의 신규 영상을 RSS로 수집하고, 자막을 Codex 기사로 재구성해 로컬에서 확인하는
FastAPI + SQLite + HTMX 앱입니다.

## 빠른 시작

요구사항은 Python 3.11+, `uv` 0.7+입니다. 다운로드에는 `ffmpeg`, 기사 생성에는 `codex` CLI가
필요합니다.

macOS/Linux:

```bash
uv sync
./run-dev.sh
```

Windows PowerShell:

```powershell
uv sync
.\run-dev.ps1
```

앱은 기본적으로 `http://127.0.0.1:48080`에서 실행됩니다.
실행 시 선택된 config의 DB를 자동으로 초기화합니다. 서버를 시작하지 않고 개발 DB만
초기화하려면 다음처럼 config를 명시합니다.

```bash
APP_CONFIG_FILE=config.dev.yaml uv run python scripts/init_db.py
```

## 주요 기능

- RSS 기반 신규 영상 수집
- YouTube 자막 추출과 schema-validated Codex 기사 생성
- 카테고리별 처리 단계 (`off`, `transcript_only`, `full`)
- 영상 다운로드와 수동 기사·자막 작업 queue
- transcript·article SQLite FTS5 검색
- 채널 일괄 추가와 Google Takeout import
- Telegram 알림과 한국어/English UI

## 실행과 설정

```bash
./run-dev.sh
./run-prod.sh
```

`config.dev.yaml`과 `config.prod.yaml`은 버전 관리되는 기본 설정입니다. 적용 순서는
`환경변수 override > APP_CONFIG_FILE YAML > 코드 기본값`입니다. 선택한 YAML은 기본 설정 위에
merge되지 않으므로, 개인 설정은 전체 기본 파일을 복사한 뒤 수정합니다.

```bash
cp config.dev.yaml config.dev.local.yaml
APP_CONFIG_FILE=config.dev.local.yaml ./run-dev.sh
```

앱은 `.env`를 자동으로 읽지 않습니다. 설정 화면에서 저장한 LLM, worker, Telegram, download,
retention, page-size 값은 SQLite `app_settings`에 유지됩니다.

## 원격 동기화

Remote sync는 category, channel, video, transcript, article만 Postgres mirror에 공유합니다. 로컬
설정, queue 상태, 다운로드 파일과 경로는 공유하지 않습니다. DSN은 YAML이 아닌 환경변수로만
전달합니다.

```bash
export BRIEFTUBE_REMOTE_SYNC_DSN='postgresql://user:password@host:5432/dbname'
uv run python scripts/init_remote_sync_db.py
./run-dev-remote-sync.sh
```

Windows에서는 같은 환경변수를 설정하고 `run-dev-remote-sync.ps1`을 실행합니다. 운영 설정은
각각 `run-prod-remote-sync.sh`와 `run-prod-remote-sync.ps1`을 사용합니다. DSN 누락 시 전용
script는 즉시 중단하며, 원격 장애나 schema 불일치는 sync만 비활성화하고 로컬 앱은 계속
실행합니다. 상태는 `/api/settings`의 `remote_sync`에서 확인할 수 있습니다.

## macOS LaunchAgent

```bash
./scripts/install-launchd-prod.sh
./scripts/restart-launchd-prod.sh
./scripts/uninstall-launchd-prod.sh
```

실제 상태를 바꾸지 않는 검토는 다음 명령을 사용합니다.

```bash
BRIEFTUBE_LAUNCHD_DRY_RUN=1 ./scripts/install-launchd-prod.sh
```

## 구조

| 경로 | 역할 |
|---|---|
| `app/main.py`, `app/state.py` | 앱과 worker 조립 |
| `app/routers/` | page, JSON API, HTMX fragment route |
| `app/workers/` | background job 실행 |
| `app/domains/`, `app/services/` | use case와 외부 I/O |
| `app/repositories/` | SQLite와 remote sync DB 접근 |
| `app/templates/`, `app/static/` | Jinja2 + HTMX UI |
| `scripts/`, `run-*` | DB 초기화와 플랫폼별 실행·운영 |

개발 규칙과 검증 명령은 [AGENTS.md](./AGENTS.md), [CONTRIBUTING.md](./CONTRIBUTING.md)를
확인하세요.
