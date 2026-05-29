# BriefTube

YouTube 채널의 신규 영상을 RSS로 수집하고, 자막을 LLM 기사로 재구성해 로컬에서 확인하는 FastAPI + SQLite + HTMX 웹 앱.

## 빠른 시작

macOS / Linux:

```bash
uv sync
uv run python scripts/init_db.py
./run-dev.sh
```

Windows PowerShell:

```powershell
uv sync
uv run python .\scripts\init_db.py
.\run-dev.ps1
```

- 앱: `http://127.0.0.1:48080`
- 작업 시작 전 운영 규칙: [AGENTS.md](./AGENTS.md)
- 기여/검증 기준: [CONTRIBUTING.md](./CONTRIBUTING.md)

## 주요 기능

- RSS 기반 신규 영상 자동 감지 및 수집
- YouTube 자막 추출 + LLM 기사 재구성
- 카테고리별 처리 단계 (`off`, `transcript_only`, `full`)
- 영상 다운로드 큐, 수동 기사화/자막 등록 큐
- 전문 검색 (자막 + 기사, SQLite FTS5)
- 채널 일괄 추가 (텍스트 입력 / Google Takeout 파일)
- Telegram 알림, 한국어/English 전환

## 요구사항

- Python 3.11+
- `uv` 0.7+
- 다운로드 기능: `ffmpeg`
- LLM 기능: `codex` 또는 `claude` CLI

## 실행

개발 모드는 `config.dev.yaml`, 운영 모드는 `config.prod.yaml`을 기본 설정 파일로 사용합니다. 서버 바인딩도 YAML의 `server_host`, `server_port`, `server_reload`에서 읽습니다.

```bash
./run-dev.sh
./run-prod.sh
```

```powershell
.\run-dev.ps1
.\run-prod.ps1
```

PowerShell 스크립트는 `uv`를 우선 사용하고, 없으면 `.venv\Scripts\python.exe` → `python` → `py` 순으로 fallback 합니다.

macOS LaunchAgent 운영:

```bash
./scripts/install-launchd-prod.sh
./scripts/restart-launchd-prod.sh
./scripts/uninstall-launchd-prod.sh
```

LaunchAgent 기본 라벨은 `BriefTube.prod`이고, 앱 주소는 `config.prod.yaml`의 `server_host`/`server_port`를 따릅니다. 실제 시스템 변경 없이 검토하려면 `BRIEFTUBE_LAUNCHD_DRY_RUN=1`을 붙여 실행합니다.

## 구조

| 경로 | 역할 |
|---|---|
| `app/main.py`, `app/state.py`, `app/config.py`, `app/database.py` | 앱 조립, 설정, DB 초기화/점진 마이그레이션 |
| `app/routers/` | `/api` JSON, `/views` HTMX fragment, 전체 페이지 라우트 |
| `app/workers/` | RSS, 자막, LLM, 다운로드, 수동 작업, 채널 메타데이터, Telegram 워커 |
| `app/services/` | 외부 I/O와 변환 로직 |
| `app/repositories/` | SQLite 접근 계층 |
| `app/templates/`, `app/static/` | Jinja2 + HTMX UI |
| `scripts/`, `run-*` | DB 초기화, 로컬 실행, macOS LaunchAgent 관리 |

설정은 `환경변수 override > APP_CONFIG_FILE yaml > 코드 기본값` 순서로 적용됩니다. 이 프로젝트는 `.env` 파일을 자동 로드하지 않으며, 기본 설정은 YAML에서 관리합니다. 전체 키와 기본값은 `app/config.py`의 `AppConfig`를 기준으로 확인합니다.

실행할 설정 파일을 바꾸려면 `APP_CONFIG_FILE`을 지정합니다. 서버 주소를 일회성으로만 바꾸려면 `SERVER_HOST`/`SERVER_PORT` 또는 기존 호환용 `HOST`/`PORT` 환경변수를 사용할 수 있습니다.

설정 화면에서 사용자가 바꾸는 값은 SQLite의 `app_settings`에 저장됩니다. 예를 들어 LLM provider/model, 워커 on/off, Telegram 저장값, 다운로드 기본 옵션, 보관 기간, 페이지 크기는 YAML 기본값과 별개로 앱 내부에서 지속됩니다.

## 테스트

검증 명령의 canonical source는 [CONTRIBUTING.md](./CONTRIBUTING.md)입니다.

```bash
uv sync
uv run python -m pytest -q
uv run python -m pytest -q -m e2e tests/e2e
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports
```

Playwright E2E 최초 실행 전에는 `uv run python -m playwright install`이 필요할 수 있습니다.

## 문서

- [AGENTS.md](./AGENTS.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)
