# BriefTube Security Audit

**감사 일자:** 2026-02-25
**감사 대상:** 전체 소스 코드 (`app/`, `scripts/`, `sql/`, `config*.yaml`, `pyproject.toml`)
**감사 도구:** 정적 분석 (수동 코드 리뷰)

---

## 1. 감사 범위 및 전제조건

### 범위

| 레이어 | 대상 파일 |
|--------|----------|
| 진입점 / 설정 | `main.py`, `config.py`, `state.py`, `database.py`, `scripts/run-*.sh` |
| 라우터 | `routers/api.py`, `routers/views.py`, `routers/pages.py` |
| 워커 | `workers/poller.py`, `workers/transcript_worker.py`, `workers/llm_worker.py`, `workers/notifier_worker.py` |
| 서비스 | `services/telegram.py`, `services/transcript.py`, `services/rss.py`, `services/channel_resolver.py`, `services/bulk_channels.py` |
| 데이터 | `repository.py`, `sql/schema.sql` |
| 프론트엔드 | `templates/**/*.html`, `template_context.py` |

### 전제조건

- 단일 프로세스, asyncio 기반 로컬 웹 앱 (단일 사용자 설계)
- SQLite 단일 파일 DB, WAL 모드
- 외부 의존: YouTube RSS/Data API, 로컬 LLM CLI(Codex/Claude), Telegram Bot API
- 인증/인가 메커니즘 없음 (설계상)

### 심각도 기준

| 등급 | 정의 |
|------|------|
| **Critical** | 운영 중 데이터 손실 또는 서비스 무음 장애를 유발할 수 있음 |
| **High** | 시크릿 노출, 외부 공격자에 의한 파괴적 조작, 또는 워커 영구 정지 |
| **Medium** | 서비스 불안정(500 에러), 리소스 소진, 또는 방어 계층 부재 |
| **Low** | 현재 안전하나 코드 변경 시 취약해질 수 있는 fragile 패턴 |

---

## 2. 인터넷 노출

### NET-1. 프로덕션 0.0.0.0 바인딩 + 인증 부재 — High

| 항목 | 내용 |
|------|------|
| **파일** | `scripts/run-prod.sh:8`, `pyproject.toml` `[tool.uvicorn]` |
| **심각도** | High |

**문제:** `run-prod.sh`는 `HOST="${HOST:-0.0.0.0}"`으로 기본 바인딩한다. 인증이 전혀 없으므로 동일 네트워크의 누구든 모든 엔드포인트에 접근 가능하다.

**재현:**
```bash
# 원격에서 전체 데이터 삭제
curl -X POST http://<서버IP>:8000/retention/delete-all
```

**권장 조치:**
- `run-prod.sh` 기본값을 `127.0.0.1`로 변경
- 외부 노출 시 리버스 프록시(nginx/Caddy) + HTTP Basic Auth 필수화
- `pyproject.toml`의 `[tool.uvicorn]` host도 `127.0.0.1`로 변경

---

### NET-2. CSRF 보호 없음 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `routers/pages.py:166` (`/retention/delete-all`), `routers/views.py:46` (`/channels/delete-selected`) 외 다수 |
| **심각도** | Medium (NET-1이 수정되면 Low로 하향) |

**문제:** 세션/쿠키 인증이 없어 전통적 CSRF는 성립하지 않으나, 서버가 0.0.0.0으로 노출된 상태에서 악의적인 웹페이지가 사용자 브라우저를 통해 폼 POST를 제출할 수 있다.

**재현:**
```html
<!-- 악의적 페이지 -->
<form action="http://192.168.1.100:8000/retention/delete-all" method="POST">
<script>document.forms[0].submit();</script>
```

**권장 조치:** 상태 변경 POST 엔드포인트에서 `HX-Request: true` 헤더 또는 `content-type: application/json` 검증 미들웨어 추가.

---

### NET-3. 보안 응답 헤더 없음 — Low

| 항목 | 내용 |
|------|------|
| **파일** | `main.py` (미들웨어 없음) |
| **심각도** | Low |

**문제:** `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` 헤더가 설정되어 있지 않다. 클릭재킹 및 MIME 스니핑 방어 계층이 없다.

**권장 조치:** Starlette `BaseHTTPMiddleware`로 최소 보안 헤더 추가.

---

## 3. 워커 안정성

### WRK-1. asyncio.create_task 워커 무음 종료 — Critical

| 항목 | 내용 |
|------|------|
| **파일** | `main.py:74-79` |
| **심각도** | Critical |

**문제:** 4개 워커 태스크가 `lifespan` 로컬 변수 `tasks`에만 저장된다. 워커가 처리되지 않은 예외로 종료되어도 앱은 HTTP 요청에 정상 응답하며, 백그라운드 처리만 영구 중단된다. 워커 내부 `while True + except Exception`이 대부분의 예외를 잡지만, **루프 진입 전 초기화 단계**(WRK-2 참조)의 예외는 잡히지 않는다.

**재현:**
1. DB가 손상된 상태로 앱 시작
2. `run_transcript_fetcher` 초기화 중 `get_transcript_guard_state` 실패
3. 태스크 예외로 종료 → `main.py`는 감지 불가
4. HTTP는 200 반환, 자막 수집은 영구 중단

**권장 조치:**
```python
def _on_worker_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        logger.critical("Worker '%s' crashed: %s", task.get_name(), exc, exc_info=exc)

# lifespan 내부:
for t in tasks:
    t.add_done_callback(_on_worker_done)
```

---

### WRK-2. transcript_worker 초기화 코드가 루프 밖 위치 — High

| 항목 | 내용 |
|------|------|
| **파일** | `workers/transcript_worker.py:166-168` |
| **심각도** | High |

**문제:** `run_transcript_fetcher`의 DB 호출 `get_transcript_guard_state(state.db)` (라인 166)이 `while True` 루프 진입 전에 위치한다. 이 호출이 실패하면 함수 전체가 예외를 던지고 WRK-1에 의해 무음 종료된다.

**재현:** DB 잠금 또는 WAL 파일 손상 상태에서 앱 시작 → 자막 워커가 처음부터 실행되지 않음.

**권장 조치:**
```python
try:
    persisted = await repository.get_transcript_guard_state(state.db)
    guard = TranscriptGuardState.from_repository(persisted)
except Exception:
    logger.exception("Failed to load transcript guard state, using defaults")
    guard = TranscriptGuardState()
```

---

### WRK-3. _save_guard_state 실패 시 예외 경로 혼재 — High

| 항목 | 내용 |
|------|------|
| **파일** | `workers/transcript_worker.py:245-246` |
| **심각도** | High |

**문제:** transcript 가져오기 성공 후 `_save_guard_state(state, guard)` (라인 245)에서 DB 쓰기가 실패하면, 예외가 라인 246의 `except Exception as exc` 블록으로 전파된다. 이 블록은 transcript fetch 오류 처리용으로 설계되어 있어, DB 저장 실패를 `_is_no_subtitle_error` 또는 `_is_hard_throttle_error`로 잘못 분류할 수 있다. 또한 in-memory `guard` 상태는 이미 변경되었지만 DB에는 기록되지 않아 재시작 시 롤백된다.

**권장 조치:** `_save_guard_state` 호출을 별도 `try/except`로 분리:
```python
try:
    await _save_guard_state(state, guard)
except Exception:
    logger.warning("Failed to persist guard state (non-fatal)", exc_info=True)
```

---

### WRK-4. Telegram 알림 배치 영구 손실 — High

| 항목 | 내용 |
|------|------|
| **파일** | `workers/notifier_worker.py:38-55`, `services/telegram.py:31` |
| **심각도** | High |

**문제:** `notification_queue.get()`으로 배치 아이템을 소비한 후 `telegram_notifier.send()`가 실패하면, 해당 배치 전체가 영구 손실된다. `raise_for_status()` (telegram.py:31)가 HTTP 4xx/5xx에서 예외를 발생시키고, 외부 `except Exception` (라인 54)이 이를 잡아 로깅만 한다. 429 (Too Many Requests)를 포함한 모든 실패에서 재시도 없이 소실된다.

**권장 조치:** 전송 실패 시 배치를 큐에 재삽입하거나, 최소한 실패한 메시지 내용을 WARNING 레벨로 로깅.

---

### WRK-5. thumbnail 다운로드 실패가 transcript 저장 차단 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `services/transcript.py:24-35`, `workers/transcript_worker.py:220-238` |
| **심각도** | Medium |

**문제:** `download_thumbnail`에서 `httpx.TimeoutException`, `httpx.ConnectError`, `OSError` (디스크 풀) 등의 예외가 발생하면, transcript_worker의 `except Exception as exc` 블록으로 전파된다. 이때 이미 성공적으로 가져온 transcript가 저장되지 않고 재시도로 스케줄링된다.

**재현:** 썸네일 서버(i.ytimg.com) 타임아웃 → 이미 받은 자막 버림 → 불필요한 재시도.

**권장 조치:** `download_thumbnail` 내부에서 모든 예외를 잡아 `None` 반환, 또는 transcript_worker에서 thumbnail 다운로드를 별도 try/except로 분리.

---

### WRK-6. pop_pending_transcript_videos 원자적 팝 아님 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `repository.py:416-438` |
| **심각도** | Medium |

**문제:** `SELECT`만 수행하고 상태를 `processing`으로 변경하지 않는다. 현재 단일 asyncio 루프이므로 실제 동시성 문제는 없으나, 앱 재시작 시 처리 중이던 자막이 중복 시도된다. `recover_stuck_jobs`는 `restructure_status = 'processing'`만 복구하고 transcript는 복구 대상이 아니다.

**권장 조치:** SELECT 후 즉시 `transcript_status = 'processing'`으로 UPDATE하는 원자적 팝 구현, 또는 현재 아키텍처에서는 문서로 제한사항 명시.

---

## 4. 입력 검증 / 크래시 유발 경로

### INP-1. FTS5 MATCH 잘못된 구문 → 500 에러 — High

| 항목 | 내용 |
|------|------|
| **파일** | `repository.py:341-372`, `routers/views.py:143`, `routers/api.py:222` |
| **심각도** | High |

**문제:** `search_documents()`는 파라미터 바인딩(`?`)을 사용하므로 SQL 인젝션은 불가능하다. 그러나 SQLite FTS5는 바인딩된 값도 자체 쿼리 문법으로 파싱하므로, 잘못된 FTS5 표현식이 `sqlite3.OperationalError`를 발생시킨다. 이 예외가 처리되지 않아 FastAPI 500 응답이 반환된다.

**재현:**
```
GET /views/search-results?q="unclosed+quote
GET /api/search?q=AND+OR+NOT
```

**권장 조치:**
```python
async def search_documents(db, query, limit=20):
    try:
        cursor = await db.execute("""...""", (query, query, limit))
        rows = await cursor.fetchall()
        return _rows_to_dicts(rows)
    except Exception:
        return []  # FTS5 문법 에러 시 빈 결과
```

---

### INP-2. 파일 업로드 크기 제한 없음 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `routers/api.py:78-82`, `routers/views.py:171-172` |
| **심각도** | Medium |

**문제:** takeout 파일 업로드에 크기 제한이 없다. `await upload.read()`가 전체 내용을 메모리에 올린 후 `json.loads()`로 파싱한다. 대용량 파일로 메모리 소진 가능.

또한 `parse_bulk_text_inputs(bulk_text)` 및 `resolve_bulk_inputs()`도 입력 수 제한이 없어, 수만 줄 입력 시 각 항목마다 YouTube HTTP 요청이 순차 발생한다.

**권장 조치:** 파일 크기 5MB 제한, bulk 입력 50건 제한.

---

### INP-3. channel_id 형식 검증 없음 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `routers/api.py:38-60`, `repository.py:102-103` |
| **심각도** | Medium |

**문제:** `POST /api/channels`에서 `channel_id`에 형식 검증이 없다. 임의 문자열이 채널 ID로 저장되고, `repository.py:103`에서 `f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"`로 RSS URL이 생성된다. 쿼리 파라미터를 포함한 문자열이 입력되면 RSS URL이 오염된다.

**권장 조치:** YouTube 채널 ID 정규식 검증: `^UC[-_A-Za-z0-9]{22}$`

---

### INP-4. 검색 쿼리 길이 제한 없음 — Low

| 항목 | 내용 |
|------|------|
| **파일** | `routers/api.py:222`, `routers/views.py:143` |
| **심각도** | Low |

**문제:** `/api/search`는 `min_length=1`만 있고 `max_length`가 없다. `/views/search-results`는 제한이 전혀 없다. 극단적으로 긴 검색어의 FTS5 파싱 오버헤드 증가.

**권장 조치:** 양쪽 모두 `max_length=500` 추가.

---

## 5. 방어적 코딩

### DEF-1. Telegram 봇 토큰 에러 로그 노출 — High

| 항목 | 내용 |
|------|------|
| **파일** | `services/telegram.py:12`, `workers/notifier_worker.py:55` |
| **심각도** | High |

**문제:** `TelegramNotifier`는 토큰을 URL에 내장하여 `self.url`로 저장한다 (라인 12). `raise_for_status()` 실패 시 httpx의 `HTTPStatusError` 메시지에 요청 URL 전체(토큰 포함)가 포함된다:

```
Client error '401 Unauthorized' for url 'https://api.telegram.org/bot<TOKEN>/sendMessage'
```

`logger.exception("Notifier loop failed")` (라인 55)가 전체 스택 트레이스를 로그에 기록하므로, 로그 접근 권한이 있는 누구든 토큰을 확인할 수 있다.

**권장 조치:** `send()` 메서드 내에서 `HTTPStatusError`를 잡아 토큰이 제거된 메시지로 재발생시키거나, `self.url`에 토큰을 저장하지 않고 호출 시마다 구성.

---

### DEF-2. Telegram 메시지 HTML escape 누락 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `workers/notifier_worker.py:12-24` |
| **심각도** | Medium |

**문제:** `_format_batch_message`에서 LLM 생성 `title`과 `lead`를 HTML escape 없이 Telegram HTML 메시지에 삽입한다 (라인 17: `f"📰 <b>{item['title']}</b>"`). LLM 출력에 `<`, `>`, `&` 등이 포함되면 Telegram API가 400 에러로 거부하여 알림 전송이 실패한다.

**권장 조치:** `html.escape()` 적용.

---

### DEF-3. sort/order 파라미터 URL 미정규화 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `templates/fragments/video_list.html:77-92` |
| **심각도** | Medium |

**문제:** 페이지네이션 URL 구성 시 `pager.sort`와 `pager.order` 값이 검증 없이 직접 삽입된다. `repository.list_videos`에서 화이트리스트 검증이 이루어지지만, 라우터에서 pagination 컨텍스트에 전달할 때는 원본 값이 그대로 전달된다. Jinja2 autoescaping이 XSS를 막지만, `hx-push-url`로 인해 브라우저 주소창 URL이 오염될 수 있다.

**권장 조치:** 라우터에서 sort/order를 화이트리스트로 정규화한 후 pagination 컨텍스트에 삽입.

---

### DEF-4. config.prod.yaml이 .gitignore에 미포함 — Medium

| 항목 | 내용 |
|------|------|
| **파일** | `.gitignore`, `config.prod.yaml` |
| **심각도** | Medium |

**문제:** 현재 `config.prod.yaml`에는 시크릿이 비어 있으나, `.gitignore`에 포함되어 있지 않다 (`config.local.yaml`만 제외). 운영자가 실수로 API 키를 직접 입력하고 `git add .`하면 Git 히스토리에 영구 기록된다.

**권장 조치:** `.gitignore`에 `config.prod.yaml` 추가, 또는 시크릿은 반드시 환경변수로만 주입하도록 문서화.

---

### DEF-5. PRAGMA table_info f-string — Low

| 항목 | 내용 |
|------|------|
| **파일** | `database.py:29` |
| **심각도** | Low |

**문제:** `f"PRAGMA table_info({table})"` 에서 `table`이 현재 `"videos"` 리터럴로만 호출되어 안전하나, 함수 시그니처가 임의 문자열을 받는 형태라 미래 확장 시 PRAGMA injection 가능.

**권장 조치:** `_ALLOWED_TABLES` frozenset 화이트리스트 추가.

---

### DEF-6. 스키마 상태 값 CHECK 제약 조건 누락 — Low

| 항목 | 내용 |
|------|------|
| **파일** | `sql/schema.sql:34, 37` |
| **심각도** | Low |

**문제:** `transcript_status`와 `restructure_status` 컬럼에 DB 레벨 CHECK 제약이 없어 애플리케이션 버그 시 잘못된 값 삽입 가능.

**권장 조치:** `CHECK (transcript_status IN ('pending','done','no_subtitle'))` 등 추가. 기존 DB 마이그레이션 필요.

---

### DEF-7. thumbnail 절대 경로 DB 저장 — Low

| 항목 | 내용 |
|------|------|
| **파일** | `services/transcript.py:35`, `repository.py:49-55` |
| **심각도** | Low |

**문제:** `download_thumbnail`이 절대 경로를 반환하여 DB에 저장한다. `_thumbnail_url`이 `Path(path).name`으로 파일명만 추출하므로 현재 동작에 문제는 없으나, `thumbnail_dir` 변경 또는 DB 이전 시 경로 불일치 발생.

**권장 조치:** DB에 파일명(`{video_id}.jpg`)만 저장.

---

### DEF-8. defusedxml 미사용 — Low

| 항목 | 내용 |
|------|------|
| **파일** | `services/rss.py:2-3` |
| **심각도** | Low |

**문제:** `xml.etree.ElementTree.fromstring()`을 사용한다. CPython expat 파서는 네트워크 외부 엔티티를 기본 비활성화하고, 파싱 대상은 YouTube RSS 피드(신뢰 소스)이므로 실질적 위험은 낮다. 심층 방어 차원의 개선 사항.

**권장 조치:** `defusedxml` 패키지로 교체.

---

## 6. 문제없는 영역 (확인 완료)

### SQL 인젝션 방어

`repository.py`의 약 40개 쿼리 전부 `?` 파라미터 바인딩을 사용한다. f-string으로 삽입되는 `sort_column`과 `order_sql` (라인 186-187)은 화이트리스트(`{"upload_time", "created_at"}`, `"ASC"/"DESC"` 이분법)로 엄격히 검증된다. 동적 `IN` 절도 `["?"] * len(ids)` 플레이스홀더로 생성한다.

### 경로 순회 방지

`/thumbnails/{filename:path}` 엔드포인트 (`main.py:102-115`)에서 `Path(filename).name`으로 디렉토리 구분자를 제거하고, `safe_name != filename` 시 400을 반환한다. `views.py`와 `pages.py`의 `_cleanup_thumbnail_files`도 `target.parent != base_dir` 검사로 경로 순회를 차단한다.

### XSS 방지

Jinja2 `autoescape=True`가 기본 적용된다. 전체 템플릿에서 `|safe` 필터가 한 번도 사용되지 않는다. JavaScript 토스트 알림은 `node.textContent = message`로 텍스트 노드 삽입 (innerHTML 아님).

### SSRF 방지

`channel_resolver.py`의 `resolve_input()`이 `CHANNEL_ID_RE.fullmatch()`, `HANDLE_RE.fullmatch()`, `YOUTUBE_URL_RE.match()`로 3단계 분기를 거쳐 임의 호스트 요청을 차단한다. URL 매칭은 youtube.com 도메인으로 한정된다.

### 시크릿 하드코딩 없음

`config.dev.yaml`, `config.prod.yaml` 모두 API 키/토큰 값이 빈 문자열이다. 환경변수 오버라이드 우선순위가 올바르게 구현되어 있다. 로그에 시크릿을 직접 출력하는 `logger` 호출이 없다 (DEF-1의 간접 노출 제외).

### 입력 정규화

`config.py`의 `max()`/`min()` 범위 강제, `repository.py`의 `_parse_int_setting`/`_parse_float_setting`/`_parse_bool_setting` 안전 파서, `schedule_transcript_retry`의 `safe_delay = max(1, int(delay_seconds))` 음수 방지 모두 올바르게 구현되어 있다.

### Foreign Key + WAL 모드

`database.py:13-15`에서 `PRAGMA foreign_keys = ON`과 `PRAGMA journal_mode = WAL`이 활성화되어 데이터 무결성과 동시 읽기 성능이 보장된다.

---

## 7. 권장 수정 우선순위

| 순위 | ID | 심각도 | 요약 | 핵심 파일 |
|------|----|--------|------|-----------|
| 1 | WRK-1 | Critical | 워커 무음 종료 감지 불가 | `main.py:74-79` |
| 2 | NET-1 | High | 프로덕션 0.0.0.0 + 인증 없음 | `scripts/run-prod.sh:8` |
| 3 | DEF-1 | High | Telegram 토큰 에러 로그 노출 | `services/telegram.py:12` |
| 4 | WRK-2 | High | transcript_worker 초기화 루프 밖 | `workers/transcript_worker.py:166` |
| 5 | WRK-3 | High | guard 저장 실패 예외 경로 혼재 | `workers/transcript_worker.py:245` |
| 6 | WRK-4 | High | 알림 배치 영구 손실 | `workers/notifier_worker.py:38-55` |
| 7 | INP-1 | High | FTS5 잘못된 쿼리 → 500 에러 | `repository.py:341-372` |
| 8 | INP-2 | Medium | 파일 업로드 크기 제한 없음 | `routers/views.py:172` |
| 9 | NET-2 | Medium | CSRF 보호 없음 | `routers/pages.py:166` 등 |
| 10 | INP-3 | Medium | channel_id 형식 검증 없음 | `routers/api.py:38-60` |
| 11 | WRK-5 | Medium | thumbnail 실패가 transcript 차단 | `services/transcript.py:24-35` |
| 12 | DEF-2 | Medium | Telegram HTML escape 누락 | `workers/notifier_worker.py:17` |
| 13 | DEF-3 | Medium | sort/order URL 미정규화 | `templates/fragments/video_list.html:77` |
| 14 | DEF-4 | Medium | config.prod.yaml .gitignore 미포함 | `.gitignore` |
| 15 | WRK-6 | Medium | transcript 원자적 팝 아님 | `repository.py:416-438` |
| 16 | DEF-5 | Low | PRAGMA f-string | `database.py:29` |
| 17 | DEF-6 | Low | CHECK 제약 조건 누락 | `sql/schema.sql:34,37` |
| 18 | DEF-7 | Low | thumbnail 절대 경로 | `services/transcript.py:35` |
| 19 | DEF-8 | Low | defusedxml 미사용 | `services/rss.py:2` |
| 20 | INP-4 | Low | 검색 쿼리 길이 무제한 | `routers/api.py:222` |
| 21 | NET-3 | Low | 보안 응답 헤더 없음 | `main.py` |

**빠른 승리 (1일 이내):** WRK-1, NET-1, INP-1, DEF-1 — 각각 5-20줄 수정으로 가장 큰 위험 제거.
**중기 (1주 이내):** WRK-2~5, INP-2~3, NET-2, DEF-2~4.
**장기/선택:** DEF-5~8, INP-4, NET-3, WRK-6.
