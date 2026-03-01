# BriefTube

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-3366CC?logo=htmx&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)


YouTube 채널의 신규 영상을 자동 수집하고, 자막을 LLM으로 기사 형태로 재구성해 로컬에서 확인하는 웹 앱.

## 주요 기능

- RSS 기반 신규 영상 자동 감지 및 수집
- YouTube 자막 추출 → LLM 기사 변환 (제목, 리드, 본문, 핵심 팩트, 타임스탬프)
- 전문 검색 (자막 + 기사, SQLite FTS5)
- Telegram 알림
- 채널 일괄 추가 (텍스트 입력 / Google Takeout 파일)
- 한국어 / English 전환

## 요구사항

- Python 3.11+

## 시작하기

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python scripts/init_db.py
```

`config.dev.yaml`을 복사해 필요한 값을 채운 뒤 실행:

```bash
./scripts/run-dev.sh          # http://127.0.0.1:8000
```

운영 모드:

```bash
./scripts/run-prod.sh         # config.prod.yaml 사용
```

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

### 쉽게 이해하기: RSS 폴러 / 트랜스크립트 페처

아래 설명은 "숫자를 올리면 어떻게 되고, 내리면 어떻게 되는지"만 아주 간단히 정리한 버전입니다.

- `POLLING_INTERVAL_MINUTES`
  - 새 영상을 확인하러 가는 "점검 주기"입니다.
  - 값을 작게 하면: 더 빨리 새 영상을 찾음 (대신 요청 횟수 증가)
  - 값을 크게 하면: 요청은 줄지만, 새 영상 반영이 늦어짐

- `TRANSCRIPT_FETCH_TIMEOUT_SECONDS`
  - 자막 요청 1번을 "최대 몇 초까지 기다릴지"입니다.
  - 너무 짧으면: 느린 네트워크에서 실패가 늘어날 수 있음
  - 너무 길면: 실패 판단이 늦어져 전체 처리도 느려질 수 있음

- `TRANSCRIPT_RETRY_MAX_ATTEMPTS`
  - 자막 실패 시 "최대 몇 번 다시 시도할지"입니다.
  - 크게 하면: 복구 기회가 늘어남
  - 너무 크게 하면: 실패 영상에 시간을 오래 씀

- `TRANSCRIPT_CHANNEL_MIN_INTERVAL_SECONDS`
  - 같은 채널 영상을 연속 처리할 때 "최소 쉬는 시간"입니다.
  - 크게 하면: 한 채널에 요청이 몰리는 것을 줄여 차단 위험 완화
  - 작게 하면: 같은 채널 처리 속도는 빨라지지만 요청이 몰릴 수 있음

- `TRANSCRIPT_CHANNEL_PICK_LOOKAHEAD`
  - 자막 대기열에서 "앞에서 몇 개를 미리 보고" 다른 채널 영상을 고를지입니다.
  - 크게 하면: 채널 분산이 잘 됨 (한 채널 몰림 방지)
  - 작게 하면: 거의 순서대로 처리

- `TRANSCRIPT_CHANNEL_HARD_COOLDOWN_SECONDS`
  - 차단 신호(예: 403/429)가 오면 "그 채널 요청을 잠시 멈추는 시간"입니다.
  - 크게 하면: 차단 회복에 유리
  - 너무 크면: 해당 채널 재시도까지 오래 걸림

- `TRANSCRIPT_BREAKER_HALF_OPEN_PROBE_COUNT`
  - 차단 이후 재개할 때 "시험 요청"을 몇 번 보낼지입니다.
  - 작게(보통 1) 하면: 안전하게 천천히 재개
  - 크게 하면: 회복 확인은 빠를 수 있지만 다시 막힐 위험 증가

- `TRANSCRIPT_WORKER_LEASE_ENABLED`
  - 자막 워커를 여러 개 띄웠을 때 "한 번에 한 워커만 일하게 잠금"을 쓰는 옵션입니다.
  - `true` 권장: 중복 처리/충돌 방지

- `TRANSCRIPT_WORKER_LEASE_TTL_SECONDS`
  - 잠금 유효 시간입니다. 워커가 죽어도 이 시간이 지나면 다른 워커가 이어서 작업할 수 있습니다.
  - 너무 짧으면: 정상 동작 중에도 잠금이 자주 바뀔 수 있음
  - 너무 길면: 워커 장애 시 복구가 늦어질 수 있음

실무에서는 기본값으로 시작하고, 먼저 `POLLING_INTERVAL_MINUTES`, `TRANSCRIPT_CHANNEL_MIN_INTERVAL_SECONDS`, `TRANSCRIPT_RETRY_MAX_ATTEMPTS` 3개만 조금씩 조정해도 체감이 큽니다.

로그 파일 최대 사용량은 대략 `LOG_FILE_MAX_BYTES * (LOG_FILE_BACKUP_COUNT + 1)` 입니다.

전체 설정 키는 `app/config.py`의 `AppConfig` 참조.

## 테스트

```bash
pip install -e '.[dev]'
pytest -q
```

## 참고 문서

- [기획서](./01_기획서.md)
- [개발 스펙](./02_개발스펙.md)
