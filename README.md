# BriefTube

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-3366CC?logo=htmx&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)

YouTube 채널의 신규 영상을 자동 수집하고, 자막을 LLM으로 기사 형태로 재구성해 로컬에서 확인하는 웹 앱.

## Agent Quickstart (5분)

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/init_db.py
./scripts/run-dev.sh
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python .\scripts\init_db.py
.\scripts\run-dev.ps1
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

## 시작하기

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python scripts/init_db.py
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python .\scripts\init_db.py
```

`config.dev.yaml`을 복사해 필요한 값을 채운 뒤 실행:

macOS / Linux:

```bash
./scripts/run-dev.sh          # http://127.0.0.1:8000
```

Windows PowerShell:

```powershell
.\scripts\run-dev.ps1         # http://127.0.0.1:8000
```

운영 모드:

macOS / Linux:

```bash
./scripts/run-prod.sh         # config.prod.yaml 사용
```

Windows PowerShell:

```powershell
.\scripts\run-prod.ps1        # config.prod.yaml 사용
```

추가 의존성:

- 다운로드 기능은 `ffmpeg`가 `PATH`에 있어야 동작합니다.
- Playwright E2E를 돌리려면 최초 1회 `python -m playwright install`이 필요할 수 있습니다.

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
전체 설정 키는 `app/config.py`의 `AppConfig`를 참조합니다.

## 테스트

기본:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```

Playwright E2E (명시 실행):

```bash
python -m pytest -q -m e2e tests/e2e
```

변경 범위별 권장:

| 변경 범위 | 권장 명령 |
|---|---|
| 템플릿/프런트 공통 | `python -m pytest -q tests/test_health.py tests/test_settings_views.py tests/test_video_list.py` |
| 다운로드 도메인 | `python -m pytest -q tests/test_download_api.py tests/test_downloads_page.py tests/test_video_list.py tests/test_video_detail.py` |
| 채널/카테고리/메타데이터 | `python -m pytest -q tests/test_channel_reactivate.py tests/test_channel_list_ui.py tests/test_channel_delete.py tests/test_api_channels.py tests/test_channel_metadata.py tests/test_categories.py` |
| 수동 기사화/LLM 런타임 | `python -m pytest -q tests/test_manual_article_api.py tests/test_manual_article_queue.py tests/test_manual_article_worker.py tests/test_llm_worker_runtime.py tests/test_llm_client.py` |

## 참고 문서

- [AGENTS.md](./AGENTS.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [기획서](./01_기획서.md)
- [개발 스펙](./02_개발스펙.md)
- [워커 아키텍처](./03_워커_아키텍처.md)
