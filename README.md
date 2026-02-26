# BriefTube

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
| `OPENCLAW_API_URL` | LLM API 엔드포인트 | (없음) |
| `OPENCLAW_API_KEY` | LLM API 키 | (없음) |
| `TELEGRAM_BOT_TOKEN` | Telegram 봇 토큰 | (없음) |
| `TELEGRAM_CHAT_ID` | Telegram 채팅 ID | (없음) |
| `POLLING_INTERVAL_MINUTES` | RSS 폴링 주기 | `15` |
| `ENV` | 실행 환경 (`dev/local/development`면 DEBUG 기본) | `prod` |
| `LOG_LEVEL` | 로그 레벨 (`AUTO`면 ENV 기반 자동) | `AUTO` |
| `LOG_TO_FILE` | 파일 로그 활성화 여부 | `true` |
| `LOG_FILE_MAX_BYTES` | 파일 로테이션 최대 바이트 | `10485760` |
| `LOG_FILE_BACKUP_COUNT` | 백업 파일 개수 | `10` |
| `LOG_NOISE_WINDOW_SECONDS` | 반복 경고/에러 집계 시간창(초) | `60` |
| `LOG_NOISE_SUPPRESS_THRESHOLD` | 시간창 내 상세 출력 허용 횟수 | `1` |
| `LOG_DEPENDENCY_LEVEL` | 외부 라이브러리 로그 레벨 | `WARNING` |

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
