# BriefTube

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-3366CC?logo=htmx&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)

YouTube 채널의 신규 영상을 자동 수집하고, 자막을 LLM으로 기사 형태로 재구성해 로컬에서 확인하는 웹 앱.

## Agent Quickstart (5분)

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

- 앱: `http://127.0.0.1:8000`
- 작업 시작 전 운영 규칙: [AGENTS.md](./AGENTS.md)
- 기여/검증 규칙: [CONTRIBUTING.md](./CONTRIBUTING.md)

## 주요 기능

- RSS 기반 신규 영상 자동 감지 및 수집
- YouTube 자막 추출 + LLM 기사 재구성
- 카테고리별 처리 단계 (`off`, `transcript_only`, `full`)
- 영상 개별/다중 다운로드 큐 및 진행 이력
- 영상 개별/다중 수동 기사화 요청
- 전문 검색 (자막 + 기사, SQLite FTS5)
- Telegram 알림
- 채널 일괄 추가 (텍스트 입력 / Google Takeout 파일)
- 한국어 / English 전환

## 요구사항

- Python 3.11+
- `uv` 0.7+

## 시작하기

macOS / Linux:

```bash
uv sync
uv run python scripts/init_db.py
```

Windows PowerShell:

```powershell
uv sync
uv run python .\scripts\init_db.py
```

`config.dev.yaml`을 복사해 필요한 값을 채운 뒤 실행:

macOS / Linux:

```bash
./run-dev.sh
```

Windows PowerShell:

```powershell
.\run-dev.ps1
```

- PowerShell 실행 스크립트는 `uv`를 우선 사용하고, 없으면 `.venv\Scripts\python.exe` → `python` → `py` 순으로 fallback 합니다.

운영 모드:

macOS / Linux:

```bash
./run-prod.sh
```

Windows PowerShell:

```powershell
.\run-prod.ps1
```

- 기본값을 바꾸고 싶으면 실행 전에 `APP_CONFIG_FILE`, `HOST`, `PORT` 환경변수를 지정하면 됩니다.

macOS에서 `prod` 설정으로 로컬 상주 실행:

```bash
./scripts/install-launchd-prod.sh
```

- `config.prod.yaml`을 사용해 `launchd` 사용자 서비스로 등록합니다.
- 기본 서비스 라벨은 `BriefTube.prod`, 기본 포트는 `48000`입니다.
- 장기 실행 안정성을 위해 `--reload` 없이 실행합니다. 개발 중 코드 자동 재시작이 필요하면 `./run-dev.sh`를 그대로 사용하세요 (launchd 미등록).
- 구(舊) 라벨 `com.brieftube.server`가 설치돼 있으면 install/uninstall 실행 시 자동으로 정리됩니다.
- 실제 시스템 변경 없이 검토만 하려면 `BRIEFTUBE_LAUNCHD_DRY_RUN=1 ./scripts/install-launchd-prod.sh` 를 사용합니다.
- 제거는 아래 스크립트를 사용합니다.

```bash
./scripts/uninstall-launchd-prod.sh
```

- 재시작: `./scripts/restart-launchd-prod.sh`
- 재시작 흐름 검토: `BRIEFTUBE_LAUNCHD_DRY_RUN=1 ./scripts/restart-launchd-prod.sh`
- 제거 흐름 검토: `BRIEFTUBE_LAUNCHD_DRY_RUN=1 ./scripts/uninstall-launchd-prod.sh`

추가 의존성:

- 다운로드 기능은 `ffmpeg`가 `PATH`에 있어야 동작합니다.
- Playwright E2E를 돌리려면 최초 1회 `uv run python -m playwright install`이 필요할 수 있습니다.

## 설정

환경변수 또는 `APP_CONFIG_FILE` (key: value yaml) 로 설정. 우선순위: 환경변수 > yaml > 기본값.

| 키 | 설명 | 기본값 |
|---|---|---|
| `DB_PATH` | SQLite DB 파일 경로 | `./data.db` |
| `LLM_TIMEOUT_SECONDS` | LLM CLI 호출 타임아웃(초) | `120` |
| `TELEGRAM_BOT_TOKEN` | Telegram 봇 토큰 | (없음) |
| `TELEGRAM_CHAT_ID` | Telegram 채팅 ID | (없음) |
| `POLLING_INTERVAL_MINUTES` | RSS 폴링 주기 | `15` |
| `TRANSCRIPT_RETRY_MAX_ATTEMPTS` | 자막 수집 재시도 최대 횟수 | `8` |
| `TRANSCRIPT_FETCH_TIMEOUT_SECONDS` | 자막 수집 요청 타임아웃(초) | `45` |
| `TRANSCRIPT_CHANNEL_MIN_INTERVAL_SECONDS` | 동일 채널 연속 요청 최소 간격(초) | `180` |
| `TRANSCRIPT_CHANNEL_PICK_LOOKAHEAD` | 채널 분산 선택을 위한 후보 조회 개수 | `20` |
| `TRANSCRIPT_CHANNEL_HARD_COOLDOWN_SECONDS` | 하드 차단 시 동일 채널 일괄 지연(초) | `900` |
| `TRANSCRIPT_BREAKER_HALF_OPEN_PROBE_COUNT` | 브레이커 half-open probe 요청 횟수 | `1` |
| `TRANSCRIPT_WORKER_LEASE_ENABLED` | transcript 워커 DB 리스 잠금 사용 여부 | `true` |
| `TRANSCRIPT_WORKER_LEASE_TTL_SECONDS` | transcript 워커 리스 TTL(초) | `45` |
| `ENV` | 실행 환경 (`dev/local/development`면 DEBUG 기본) | `prod` |
| `LOG_LEVEL` | 로그 레벨 (`AUTO`면 ENV 기반 자동) | `AUTO` |
| `LOG_TO_FILE` | 파일 로그 활성화 여부 | `true` |
| `LOG_FILE_MAX_BYTES` | 파일 로테이션 최대 바이트 | `10485760` |
| `LOG_FILE_BACKUP_COUNT` | 백업 파일 개수 | `10` |
| `LOG_NOISE_WINDOW_SECONDS` | 반복 경고/에러 집계 시간창(초) | `60` |
| `LOG_NOISE_SUPPRESS_THRESHOLD` | 시간창 내 상세 출력 허용 횟수 | `1` |
| `LOG_DEPENDENCY_LEVEL` | 외부 라이브러리 로그 레벨 | `WARNING` |

로그 파일 최대 사용량은 대략 `LOG_FILE_MAX_BYTES * (LOG_FILE_BACKUP_COUNT + 1)` 입니다.
Telegram 봇 토큰과 채팅 ID는 설정 페이지에서도 SQLite에 저장할 수 있습니다. 다만 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 또는 config 파일 값이 있으면 그 값이 우선 적용됩니다.
전체 설정 키는 `app/config.py`의 `AppConfig`를 참조합니다.

## 테스트

검증 명령의 canonical source는 `CONTRIBUTING.md`입니다. 기본 실행은 다음과 같습니다.

```bash
uv sync
uv run python -m pytest -q
```

- 기본 단위 테스트는 `tests/conftest.py`에서 `TRANSCRIPT_WORKER_LEASE_ENABLED=0`으로 실행합니다. `TestClient` 단일 프로세스 환경에서 transcript lease 전용 DB 연결이 별도 쓰기 잠금을 잡아 `database is locked`를 만들 수 있기 때문입니다.
- 기본 단위 테스트는 pytest 환경에서 transcript background worker도 띄우지 않습니다. 필요하면 `BRIEFTUBE_ENABLE_TRANSCRIPT_WORKER_IN_TESTS=1`로 명시적으로 다시 켤 수 있습니다.
- lease 영향 비교가 필요하면 `TRANSCRIPT_WORKER_LEASE_ENABLED=0 uv run python -m pytest -q ...` 또는 `TRANSCRIPT_WORKER_LEASE_ENABLED=1 uv run python -m pytest -q ...`처럼 명시해 실행합니다.

Playwright E2E (명시 실행):

```bash
uv run python -m pytest -q -m e2e tests/e2e
```

정적 검사 (PR 머지 전 모두 통과 필수):

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports
```

변경 범위별 권장 명령과 위반 처리 정책은 `CONTRIBUTING.md`의 테스트/검증 기준을 우선합니다.

## 참고 문서

- [AGENTS.md](./AGENTS.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [기획서](./01_기획서.md)
- [개발 스펙](./02_개발스펙.md)
- [워커 아키텍처](./03_워커_아키텍처.md)
