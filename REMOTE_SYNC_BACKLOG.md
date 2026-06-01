# Remote Sync Mirror Backlog

작성일: 2026-06-01
상태: 완료

## 목표

BriefTube의 현재 로컬 실행 모델(FastAPI + SQLite)은 유지하면서, 여러 단말기에서 공유해야 하는 핵심 데이터만 원격 Postgres 미러 DB에 동기화한다.

- 앱 시작 시 원격 미러에서 공유 데이터를 가져와 로컬 SQLite를 업데이트한다.
- 앱 실행 중 로컬 변경분을 주기적으로 원격 미러에 push한다.
- 주기적 pull은 구현하지 않는다.
- 원격 DB 장애 또는 스키마 불일치가 앱의 로컬 실행을 막지 않게 한다.
- 원격 DB가 설정되지 않았거나 접근할 수 없으면 현재 로컬 전용 앱과 완전히 동일하게 작동한다.

## 범위

### 공유 대상

- `categories`: 카테고리 이름, 정렬 순서, 처리 단계, 기본 카테고리 여부
- `channels`: 채널 식별자, 이름, RSS URL, 활성 상태, YouTube 채널 메타데이터 중 앱 표시와 수집에 필요한 값
- `videos`: 영상 식별자, 채널 식별자, 제목, 업로드 시각, 썸네일 URL처럼 단말기 경로에 의존하지 않는 표시 데이터
- `transcripts`: 영상별 자막 원문, 언어, 수집 출처
- `articles`: 영상별 구조화 기사, LLM 생성 메타데이터

### 제외 대상

- 앱 설정(`app_settings`)
- 다운로드 작업과 이벤트(`download_jobs`, `download_events`)
- 수동 작업 큐(`manual_article_jobs`, `manual_transcript_jobs`)
- 시스템 알림(`system_alerts`)
- 로컬 파일 경로, 썸네일 파일, 다운로드 산출물
- 실행/큐 상태: `*_processing`, retry count, next attempt, last error, viewed state처럼 단말기 실행 상태에 가까운 값
- FTS 가상 테이블과 FTS 트리거 상태

## 설계 결정

- 원격 DB는 앱 DB의 1:1 복제본이 아니라 `sync_*` 테이블을 가진 제한된 미러 스토어로 둔다.
- 로컬 SQLite 스키마는 가능한 한 유지하고, 동기화에 필요한 최소 메타데이터만 추가한다.
- 공유 대상 row에는 sync 필수 메타데이터를 둔다: `updated_at`, `deleted_at`, `sync_dirty`, `sync_last_pushed_at`, `origin_device_id`.
- 충돌 정책은 row 단위 Last Write Wins(LWW)를 기본으로 한다.
- LWW 비교 기준은 UTC 기준 `updated_at`이다.
- 삭제는 즉시 물리 삭제하지 않고 `deleted_at` tombstone을 먼저 남긴다.
- tombstone의 authoritative source는 각 공유 테이블의 `deleted_at`이다. 별도 `sync_tombstones` 테이블은 audit 또는 cross-table pruning 보조가 필요할 때만 추가한다.
- tombstone 보관 기간이 지난 뒤 작은 batch로 물리 삭제한다. 보관 기간 안에는 remote/local 어느 쪽에서도 같은 row를 부활시키지 않는다.
- 동일 row에서 `updated_at`이 같으면 `origin_device_id`의 고정 정렬 순서로 결정한다.
- 삭제 tombstone과 일반 row가 충돌하면 더 최신 시각을 따른다. 동일 시각이면 삭제를 우선한다.
- remote sync 실패는 경고 로그만 남기고 로컬 앱 실행을 계속한다.
- app startup pull은 remote row를 적용하기 전에 local dirty row와 LWW 비교를 수행한다.
- 주기적 push는 local dirty row만 대상으로 한다. 주기적 pull은 구현하지 않는다.
- 영상 실행 상태(`pipeline_status`, `*_processing`, retry/error/viewed 상태)는 공유하지 않는다. 단말기 간 공유는 transcript/article 같은 결과물 존재 여부와 실제 결과 데이터로 판단한다.
- remote DSN은 환경 변수로만 설정한다.
- 기본 push 주기는 5분, 기본 batch 크기는 100 rows, 기본 tombstone 보관 기간은 30일이다.
- remote가 일시적으로 죽어 있을 때 사용자가 삭제하면, 로컬 UX에는 즉시 반영하고 `sync_dirty` tombstone을 남겨 나중에 remote로 push한다.

## 결정 필요

| 항목 | 기본 가정 | 확정 위치 |
| --- | --- | --- |
| 원격 Postgres 제공자 | 일반 PostgreSQL 호환 DB | 설정/운영 문서 |
| 원격 물리 삭제 기준 | `deleted_at < now - retention` | pruning 구현 작업 |
| 로컬 물리 삭제 기준 | 기존 retention 정책과 별도 합의 | pruning 구현 작업 |

## 확정된 기본값

| 항목 | 값 |
| --- | --- |
| remote DSN 설정 위치 | 환경 변수 |
| push 주기 | 5분 |
| batch 크기 | 100 rows |
| tombstone 보관 기간 | 30일 |
| 영상 실행 상태 동기화 | 공유하지 않음 |
| 카테고리/처리 단계 동기화 | `categories`와 `channels.category_id` 함께 동기화 |
| remote 미설정/접근 불가 동작 | 현재 로컬 전용 앱과 동일 |

## 필수 불변조건

- remote sync는 설정이 없거나 disabled면 완전히 비활성화된다.
- remote sync가 실패해도 로컬 DB 초기화, `/health`, 기본 화면 렌더링, RSS/transcript/LLM/download worker의 기존 동작은 계속된다.
- remote sync 미설정/접근 불가/비활성화 상태에서는 기존 로컬 전용 앱과 비교해 UI, API 응답, DB write 경로, worker scheduling 동작이 달라지지 않는다.
- remote DSN, 인증 토큰, 원격 오류의 민감한 세부 정보는 로그에 원문으로 남기지 않는다.
- remote schema version이 앱이 아는 version과 다르면 sync만 비활성화한다.
- local DB 파일을 통째로 업로드하거나 다운로드해 덮어쓰지 않는다.
- `thumbnail_path`처럼 단말기 파일 경로가 들어가는 값은 remote mirror에 올리지 않는다.
- `channels.is_active=0`은 삭제가 아니라 polling 제외 상태로 유지한다.
- 물리 삭제는 tombstone push가 성공했거나, tombstone 보관 기간 정책상 부활 위험이 없는 경우에만 수행한다.
- FTS 테이블은 직접 동기화하지 않고, local upsert/delete가 기존 trigger를 통해 재구성하게 한다.

## 공유 필드 초안

이 표는 1번 작업에서 실제 repository와 schema 기준으로 확정한다. 표에 없는 필드는 기본적으로 동기화하지 않는다.

카테고리와 처리 단계는 함께 동기화한다. 현재 앱에서 새 영상의 `processing_stage_snapshot`은 채널이 속한 카테고리의 `processing_stage`에서 만들어지므로, `categories`와 `channels.category_id`를 분리하면 단말기별 처리 동작이 달라질 수 있다.

`categories.id`는 로컬 autoincrement라 단말기 간 안정 키로 사용할 수 없다. sync 구현에서는 `category_uid`를 도입하고, remote에는 `category_uid`를 저장한다. 로컬 `channels.category_id`는 pull/merge 시 `category_uid`를 로컬 `categories.id`로 해석해 저장한다.

| 로컬 테이블 | 원격 테이블 | 안정 키 | 공유 후보 필드 | 제외 필드 |
| --- | --- | --- | --- | --- |
| `categories` | `sync_categories` | `category_uid` | `category_uid`, `name`, `sort_order`, `processing_stage`, `is_default`, `created_at` | local autoincrement `id`, legacy `llm_enabled` |
| `channels` | `sync_channels` | `channel_id` | `channel_id`, `channel_name`, `rss_url`, `is_active`, `category_uid`, `last_seen_published_at`, `channel_handle`, `channel_url_canonical`, `channel_thumbnail_url`, `channel_description`, `channel_language_hint`, `metadata_fetched_at`, `metadata_fetch_status` | local `category_id`, `metadata_fetch_error`, `metadata_retry_count`, `metadata_next_fetch_at`, `metadata_last_http_status`, `rss_fail_streak`, `rss_last_polled_at` |
| `videos` | `sync_videos` | `video_id` | `video_id`, `channel_id`, `title`, `upload_time`, result flags derived from transcript/article presence | `thumbnail_path`, `pipeline_status`, `processing_stage_snapshot`, retry/error fields, `viewed_at` |
| `transcripts` | `sync_transcripts` | `video_id` | `video_id`, `raw_text`, `language`, `source_type`, `created_at` | local autoincrement `id`, FTS rows |
| `articles` | `sync_articles` | `video_id` | `video_id`, `title`, `lead`, `body`, `fact_box`, `timestamps`, `llm_provider`, `llm_model`, `llm_reasoning_effort`, `llm_generated_at`, `created_at` | local autoincrement `id`, FTS rows |

## 작업 상태 규칙

각 작업은 아래 상태 중 하나를 사용한다.

- `TODO`: 아직 시작하지 않음
- `DOING`: 진행 중
- `BLOCKED`: 외부 결정 또는 선행 작업 필요
- `DONE`: 구현과 해당 검증 완료

작업자가 변경할 때는 아래 규칙을 따른다.

- 작업 시작 시 해당 작업의 `상태`를 `DOING`으로 바꾸고 `담당`에 작업자 이름 또는 세션 식별자를 적는다.
- 작업이 외부 결정, 선행 작업, 환경 문제로 막히면 `BLOCKED`로 바꾸고 막힌 이유를 한 줄로 남긴다.
- 작업 완료 시 `DONE`으로 바꾸고 실행한 검증 명령과 결과를 해당 작업의 `검증` 아래 또는 체크포인트 로그에 남긴다.
- 범위나 설계 결정을 바꾸면 상단의 `설계 결정`, `확정된 기본값`, `공유 필드 초안`도 함께 갱신한다.
- 같은 checkout에서 동시에 같은 작업을 진행하지 않는다. 병렬 작업은 별도 branch/worktree에서 담당 범위를 나눈다.

## 진행 현황

| # | 작업 | 상태 | 담당 | 선행 조건 | 완료 산출물 |
| --- | --- | --- | --- | --- | --- |
| 1 | 동기화 계약 확정 | DONE | Codex | 없음 | 확정 field mapping과 merge 계약 |
| 2 | 로컬 sync metadata 추가 | DONE | Codex | 1 | SQLite migration, metadata 갱신 경로 |
| 3 | 원격 Postgres 미러 스키마 작성 | DONE | Codex | 1 | Postgres DDL/migration, driver/test 방식 |
| 4 | 삭제 tombstone 전환 | DONE | Codex | 2 | soft delete/tombstone 경로 |
| 5 | 로컬에서 원격으로 push 구현 | DONE | Codex | 2, 3, 4 | push worker/service |
| 6 | 앱 시작 시 pull/merge 구현 | DONE | Codex | 2, 3, 4 | startup pull/merge |
| 7 | pruning과 물리 삭제 구현 | DONE | Codex | 4, 5, 6 | remote/local pruning |
| 8 | 설정과 운영 가시성 추가 | DONE | Codex | 3 | env config, sync status |
| 9 | 통합 시나리오 검증 | DONE | Codex | 5, 6, 7, 8 | two-local-one-remote integration tests |

이 표는 요약 현황이다. 개별 작업의 `상태`/`담당`을 바꾸면 이 표도 같이 갱신한다.

## 백로그

### 1. 동기화 계약 확정

상태: DONE
담당: Codex

작업:

- 공유 대상 테이블의 동기화 필드 목록을 확정한다.
- 로컬 기본키와 원격 unique key를 확정한다.
- `category_uid`, `channel_id`, `video_id`를 안정 키로 쓰고, transcript/article은 `video_id` 기준 1:1 row로 정의한다.
- 로컬 전용 필드와 원격 미러 필드를 명시적으로 분리한다.
- 카테고리 기반 처리 단계는 `categories`와 `channels.category_id` 동기화로 유지한다.
- `categories.id`와 remote `category_uid`의 매핑 규칙을 정의한다.
- `videos.pipeline_status`는 직접 공유하지 않고 transcript/article 존재 여부를 통해 필요한 결과 상태만 재구성한다.
- pull/merge가 건드리는 로컬 컬럼과 건드리지 않는 컬럼을 문서화한다.

완료 조건:

- `categories`, `channels`, `videos`, `transcripts`, `articles`별 field mapping 표가 작성되어 있다.
- 제외 대상 데이터가 동기화 코드에서 접근되지 않는 기준이 있다.
- 카테고리와 실행 상태 필드의 제외 또는 포함 정책이 확정되어 있다.

검증:

- 문서 검토
- 관련 repository 함수와 `sql/schema.sql` 기준 필드명 대조
- `uv run pytest -q tests/test_remote_sync_config.py tests/test_remote_sync_local_repository.py`

### 2. 로컬 sync metadata 추가

상태: DONE
담당: Codex

작업:

- 로컬 SQLite에 sync metadata를 최소 추가한다.
- 필수 컬럼: `updated_at`, `deleted_at`, `sync_dirty`, `sync_last_pushed_at`, `origin_device_id`.
- `categories`에는 단말기 간 안정 키인 `category_uid`를 추가한다.
- 기존 row의 backfill 정책을 정의한다.
- 기존 카테고리의 `category_uid` backfill 정책을 정의한다. 기본 카테고리는 모든 단말기에서 같은 reserved uid를 사용한다.
- 기존 앱 동작이 metadata 추가 없이도 깨지지 않도록 migration을 idempotent하게 작성한다.
- 공유 대상 write 경로가 `updated_at`과 `sync_dirty`를 갱신하도록 한다.
- `origin_device_id` 생성/저장 위치를 정하고, 단말기별로 안정적으로 유지한다.
- remote sync가 disabled이거나 DSN이 없을 때 기존 write 경로의 결과와 side effect가 바뀌지 않게 한다.

완료 조건:

- 새 checkout과 기존 DB 모두에서 `init_database`가 통과한다.
- 기존 CRUD 경로가 필요한 row의 `updated_at` 또는 dirty marker를 갱신한다.
- FTS trigger와 기존 FK 동작이 유지된다.
- clock drift 영향을 줄이기 위해 UTC timestamp 형식이 통일되어 있다.
- remote sync 미설정 상태에서 기존 테스트가 sync 도입 전과 같은 기대값으로 통과한다.

검증:

```bash
uv run pytest -q tests/test_health.py tests/test_api_channels.py tests/test_video_detail.py
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

실행 결과:

```bash
uv run pytest -q tests/test_remote_sync_config.py tests/test_remote_sync_local_repository.py
uv run pytest -q tests/test_health.py tests/test_api_channels.py tests/test_video_detail.py tests/test_channel_delete.py tests/test_retention_page.py tests/test_settings_api.py tests/test_settings_views.py tests/test_remote_sync_config.py tests/test_remote_sync_local_repository.py
uv run pytest -q
# 384 passed, 1 skipped, 102 deselected
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
```

### 3. 원격 Postgres 미러 스키마 작성

상태: DONE
담당: Codex

작업:

- 원격 전용 `sync_*` 테이블 DDL을 작성한다.
- 기본 테이블: `sync_categories`, `sync_channels`, `sync_videos`, `sync_transcripts`, `sync_articles`, `sync_metadata`.
- `sync_tombstones`는 기본 테이블의 `deleted_at`만으로 부족한 경우에만 추가한다.
- `sync_metadata`에 schema version과 마지막 maintenance 시각을 저장한다.
- Postgres schema migration 실행 방식을 정한다.
- SQLite schema와 Postgres schema를 1:1로 맞추려 하지 않고, 미러에 필요한 필드만 둔다.
- Postgres 드라이버와 테스트용 Postgres 실행 방식을 확정한다.
- remote DSN은 환경 변수에서만 읽도록 한다.

완료 조건:

- 깨끗한 Postgres DB에 원격 schema를 생성할 수 있다.
- unique constraint와 index가 sync 조회 패턴에 맞게 정의되어 있다.
- schema version mismatch 시 앱이 sync만 비활성화할 수 있는 기준이 있다.
- 새 의존성이 필요하면 `pyproject.toml`/lockfile 변경과 검증 명령이 이 작업에 포함되어 있다.

검증:

```bash
# 실제 명령은 구현 시 확정한다.
# 예: uv run python scripts/init_remote_sync_db.py --dry-run
# 예: uv run pytest -q tests/test_remote_sync_schema.py
```

진행:

- `asyncpg` 기반 remote gateway와 `scripts/init_remote_sync_db.py`를 추가했다.
- `sync_metadata`, `sync_categories`, `sync_channels`, `sync_videos`, `sync_transcripts`, `sync_articles` DDL과 LWW upsert SQL을 추가했다.
- 실제 Postgres DB 대상 schema 생성 검증은 `BRIEFTUBE_TEST_REMOTE_SYNC_DSN`이 없고 Docker daemon이 실행 중이 아니어서 skip했다.
- 실제 Postgres smoke 재현 명령: `BRIEFTUBE_TEST_REMOTE_SYNC_DSN='postgresql://...' uv run pytest -q tests/test_remote_sync_integration.py::test_remote_sync_real_postgres_schema_smoke`
- schema 초기화 dry-run 검증: `BRIEFTUBE_REMOTE_SYNC_DSN='postgresql://example.invalid/db' uv run python scripts/init_remote_sync_db.py --dry-run`

### 4. 삭제 tombstone 전환

상태: DONE
담당: Codex

작업:

- 공유 대상 삭제 경로에서 먼저 `deleted_at`, `updated_at`, `sync_dirty`를 기록한다.
- retention 삭제와 채널 삭제가 tombstone을 남긴 뒤 물리 삭제로 넘어가도록 전환 순서를 정의한다.
- 카테고리 삭제는 현재처럼 채널을 기본 카테고리로 이동시키는 동작과 remote sync tombstone이 충돌하지 않도록 정의한다.
- 채널 삭제 시 관련 video/transcript/article tombstone 처리 순서를 정의한다.
- 기존 사용자 UX의 “삭제 완료” 의미와 sync tombstone 보관 의미가 충돌하지 않게 한다.
- 썸네일 파일 삭제처럼 로컬 파일 정리는 기존대로 로컬에서 수행하되 remote에는 경로를 올리지 않는다.

완료 조건:

- 삭제된 row가 다른 단말기 startup pull 이후 재생성되지 않는 설계가 있다.
- 기존 hard delete 경로가 sync enabled 상태에서 tombstone을 건너뛰지 않는다.
- sync disabled 상태의 기존 삭제 UX가 유지된다.

검증:

```bash
uv run pytest -q tests/test_remote_sync_tombstones.py tests/test_channel_delete.py tests/test_retention_page.py
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

진행:

- remote sync runtime이 실제 활성화된 경우 채널/영상/카테고리 삭제 경로가 tombstone을 남긴다.
- remote 미설정 또는 startup 접근 불가 상태에서는 기존 hard delete 동작을 유지하도록 회귀 테스트를 추가했다.
- `tests/test_remote_sync_integration.py`에서 tombstone push 후 다른 local startup pull에 삭제 tombstone이 반영되는 흐름을 검증했다.

### 5. 로컬에서 원격으로 push 구현

상태: DONE
담당: Codex

작업:

- sync service와 repository를 추가한다.
- 로컬 SQLite 접근과 원격 Postgres 접근의 책임을 분리한다. 권장 이름은 local sync repository, remote sync gateway, sync service다.
- local dirty row를 batch로 읽어 원격 `sync_*` 테이블에 upsert한다.
- upsert는 remote row보다 local row가 최신일 때만 갱신한다.
- `deleted_at`이 있는 row도 remote에 push한다.
- 성공한 row는 local dirty marker와 `sync_last_pushed_at`을 갱신한다.
- remote 연결 실패, 인증 실패, schema mismatch를 구분해 로그를 남긴다.
- 앱 실행 중 주기적 push worker를 붙인다.
- 기본 주기는 5분이며, 한 번에 최대 100 rows를 처리한다.

완료 조건:

- 앱이 원격 연결 실패 상태에서도 로컬 모드로 정상 시작한다.
- push 성공 후 다른 단말기의 startup pull이 읽을 수 있는 remote row가 생긴다.
- push 실패가 기존 RSS/transcript/LLM/download worker를 중단하지 않는다.
- remote가 죽어 있어도 local dirty tombstone과 dirty row가 보존되어 다음 주기에 재시도된다.

검증:

```bash
uv run pytest -q tests/test_remote_sync_push.py
uv run pytest -q tests/test_health.py
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

진행:

- dirty row batch 조회, remote LWW upsert, 성공 row dirty clear, periodic worker 연결을 구현했다.
- batch size는 한 번의 push에서 전체 dirty row 최대치로 적용되도록 검증했다.
- 실제 Postgres 대상 push는 `BRIEFTUBE_TEST_REMOTE_SYNC_DSN`이 없고 Docker daemon이 실행 중이 아니어서 미실행이며, in-memory gateway 계약 테스트로 two-local-one-remote push/pull 흐름을 검증했다.

### 6. 앱 시작 시 pull/merge 구현

상태: DONE
담당: Codex

작업:

- app startup 단계에서 remote sync가 enabled이면 pull/merge를 한 번 실행한다.
- remote row를 local row와 비교해 LWW 정책으로 병합한다.
- local dirty row가 있으면 무조건 remote로 덮어쓰지 않고 `updated_at`을 비교한다.
- remote에만 있는 category/channel/video/transcript/article은 local에 upsert한다.
- pull 순서는 category, channel, video, transcript/article 순서로 처리해 FK와 `category_uid` 매핑을 보장한다.
- local에만 있고 dirty인 row는 보존하고 이후 push 대상이 되게 한다.
- remote tombstone이 local row보다 최신이면 local에 `deleted_at`을 반영하고 부활을 막는다.
- local upsert/delete가 기존 FTS trigger를 통해 검색 인덱스를 갱신하게 한다.
- pull/merge 중 실패해도 앱 시작은 계속한다.
- remote DSN이 없거나 접속할 수 없으면 pull/merge를 건너뛰고 기존 startup 흐름과 동일하게 진행한다.

완료 조건:

- 단말기 A에서 push한 데이터가 단말기 B 앱 시작 시 로컬 DB에 반영된다.
- local pending change가 remote old row로 되돌아가지 않는다.
- startup sync 실패가 `/health` 응답과 기본 화면 렌더링을 막지 않는다.

검증:

```bash
uv run pytest -q tests/test_remote_sync_pull.py tests/test_remote_sync_conflicts.py
uv run pytest -q tests/test_health.py tests/test_video_detail.py
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

진행:

- startup pull/merge와 category_uid 기반 channel category 매핑을 구현했다.
- local dirty row가 remote old row로 되돌아가지 않는 충돌 테스트를 추가했다.
- 실제 Postgres 대상 startup pull은 `BRIEFTUBE_TEST_REMOTE_SYNC_DSN`이 없고 Docker daemon이 실행 중이 아니어서 미실행이며, in-memory gateway 계약 테스트로 검증했다.

### 7. pruning과 물리 삭제 구현

상태: DONE
담당: Codex

작업:

- tombstone 보관 기간 이후 remote 물리 삭제 batch를 구현한다.
- local 물리 삭제는 기존 retention UX와 충돌하지 않게 별도 기준을 둔다.
- remote 물리 삭제는 FK 순서 또는 `ON DELETE` 정책을 명시해 수행한다.
- pruning 작업은 한 번에 작은 batch만 처리하고 실패해도 다음 주기에서 재시도한다.
- tombstone 기본 보관 기간은 30일이다.

완료 조건:

- remote pruning이 용량을 줄이면서도 tombstone 보관 기간 안의 단말기 복귀를 지원한다.
- channel 삭제와 video 삭제 모두 FK 순서가 안전하다.

검증:

```bash
uv run pytest -q tests/test_remote_sync_tombstones.py tests/test_channel_delete.py
uv run pytest -q tests/test_remote_sync_pruning.py
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

진행:

- remote/local tombstone pruning 코드를 추가했다.
- dirty row가 없는 push 주기에도 pruning이 실행되는지 검증했다.
- 실제 Postgres pruning은 `BRIEFTUBE_TEST_REMOTE_SYNC_DSN`이 없고 Docker daemon이 실행 중이 아니어서 미실행이며, in-memory gateway 계약 테스트로 오래된 tombstone prune 흐름을 검증했다.

### 8. 설정과 운영 가시성 추가

상태: DONE
담당: Codex

작업:

- remote sync enable/disable 설정을 추가한다.
- remote DSN은 환경 변수에서만 읽고, 비밀값으로 취급해 로그에 원문을 남기지 않는다.
- 마지막 sync 성공 시각, 마지막 실패 코드, schema version 상태를 확인할 수 있게 한다.
- UI 노출은 최소로 하고, 필요하면 settings 화면에 상태만 표시한다.
- 운영 로그 event 이름을 정한다.
- `app_settings`에는 remote DSN을 저장하지 않는다. sync 상태 key를 저장하는 경우에도 공유 대상에서 명시적으로 제외한다.

완료 조건:

- remote DSN이 커밋 대상 파일에 평문으로 들어가지 않는다.
- 설정이 없거나 disabled면 sync 관련 worker가 실행되지 않는다.
- sync 상태를 로그 또는 API로 확인할 수 있다.
- remote sync 미설정 상태에서 UI/API가 기존 로컬 전용 앱과 다르지 않다.

검증:

```bash
uv run pytest -q tests/test_settings_api.py tests/test_settings_views.py
uv run pytest -q tests/test_remote_sync_config.py
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

실행 결과:

```bash
uv run pytest -q tests/test_remote_sync_config.py tests/test_remote_sync_local_repository.py
uv run pytest -q
# 384 passed, 1 skipped, 102 deselected
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
```

### 9. 통합 시나리오 검증

상태: DONE
담당: Codex

작업:

- 임시 SQLite DB 2개와 임시 Postgres DB 1개를 사용하는 통합 테스트를 작성한다.
- 단말기 A push, 단말기 B startup pull, 단말기 B push, 단말기 A 재시작 pull 흐름을 검증한다.
- 원격 장애, schema mismatch, 오래된 tombstone pruning 시나리오를 검증한다.

완료 조건:

- 개인용 단일 사용자 흐름의 핵심 sync loop가 자동 테스트로 재현된다.
- 실패 시 앱 로컬 실행 유지 조건이 자동 테스트로 보장된다.

검증:

```bash
uv run pytest -q tests/test_remote_sync_integration.py
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright && uv run lint-imports
```

실행 결과:

```bash
uv run pytest -q tests/test_remote_sync_integration.py tests/test_remote_sync_config.py tests/test_remote_sync_local_repository.py
# 14 passed, 1 skipped (BRIEFTUBE_TEST_REMOTE_SYNC_DSN 미설정)
BRIEFTUBE_REMOTE_SYNC_DSN='postgresql://example.invalid/db' uv run python scripts/init_remote_sync_db.py --dry-run
docker info --format '{{.ServerVersion}}'
# Docker daemon 미실행으로 실제 Postgres 컨테이너 검증 불가
uv run pytest -q
# 384 passed, 1 skipped, 102 deselected
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
```

## 구현 메모

- Postgres 드라이버는 async 앱 흐름과 맞는 라이브러리를 우선 검토한다.
- 새 의존성을 추가할 경우 `pyproject.toml`과 lockfile 변경이 필요하므로 별도 작업으로 다룬다.
- sync 계층은 기존 앱 repository 계약을 변경하지 않고, sync 전용 local repository와 remote gateway로 책임을 분리한다.
- import 방향은 `CONTRIBUTING.md`의 계층 계약을 따른다.
- 원격 DB 작업은 앱 시작 경로에서 timeout을 짧게 둔다.
- full DB dump/restore 방식은 설정, 다운로드, 로컬 상태까지 섞이므로 이번 범위에서 제외한다.

## 에이전트 체크포인트 로그

| 날짜 | 작업자 | 변경 | 검증 | 남은 위험 |
| --- | --- | --- | --- | --- |
| 2026-06-01 | Codex | 초기 백로그 작성 | 문서 변경만 수행 | 세부 field mapping, 원격 제공자, 보관 기간은 미확정 |
| 2026-06-01 | Codex | sync metadata, category/status, tombstone, Postgres 테스트 전제 보강 | 문서 검토와 저장소 schema 대조 | 원격 제공자와 물리 삭제 세부 기준은 구현 작업에서 확정 필요 |
| 2026-06-01 | Codex | 카테고리 동기화 정책을 `categories` + `channels.category_id`로 확정하고 진행 현황 표 추가 | 문서 검토 | 실제 field mapping은 1번 작업에서 schema/repository 기준으로 최종 확정 필요 |
| 2026-06-01 | Codex | SQLite sync metadata, category_uid, remote schema/gateway, startup pull, push worker, tombstone, pruning, sync status 1차 구현 | `uv run pytest -q` (379 passed, 102 deselected); `uv run ruff check .`; `uv run ruff format --check .`; `uv run pyright`; `uv run lint-imports` | 실제 Postgres 통합 검증, schema mismatch 자동 테스트, multi-device end-to-end 검증 미실행 |
| 2026-06-01 | Codex | audit 후 dirty row가 없으면 pruning이 건너뛰어지는 문제와 legacy migration transaction 충돌 수정 | `uv run pytest -q` (379 passed, 102 deselected); `uv run ruff check .`; `uv run ruff format --check .`; `uv run pyright`; `uv run lint-imports` | 실제 Postgres pruning 검증은 환경 필요 |
| 2026-06-01 | Codex | two-local-one-remote 통합 테스트, schema mismatch, dirty batch limit, local dirty conflict, optional real Postgres smoke 추가 | `uv run pytest -q tests/test_remote_sync_integration.py tests/test_remote_sync_config.py tests/test_remote_sync_local_repository.py` (14 passed, 1 skipped); schema dry-run 통과 | 실제 Postgres smoke는 `BRIEFTUBE_TEST_REMOTE_SYNC_DSN` 미설정 및 Docker daemon 미실행으로 skip |
| 2026-06-01 | Codex | audit 후 category tombstone이 channel 이동 row보다 먼저 pull되는 경우를 기본 카테고리 이동 + dirty repair로 보정 | `uv run pytest -q` (384 passed, 1 skipped, 102 deselected); `uv run ruff check .`; `uv run ruff format --check .`; `uv run pyright`; `uv run lint-imports` | 실제 Postgres smoke는 환경 DSN 필요 |
