# Repository rules

- application code는 `app.repositories.<domain>` 공개 facade만 import한다.
- private repository module끼리는 `app.repositories._<domain>`을 직접 import하고 package barrel을 경유하지 않는다.
- row/dict 변환, error message truncate, thumbnail URL 같은 공유 private 헬퍼는
  `app.repositories._common`만 사용한다. 동일 헬퍼를 domain 모듈에 재정의하지 않는다.
- multi-statement atomic use case는 `app.database.database_transaction`으로 전용 연결의 transaction owner가 된다.
- transaction boundary에서 호출할 repository 함수는 명시적 `commit=False`를 지원하고 기본 public 계약은 기존처럼 commit한다.
- SQLite schema 변경은 루트 `AGENTS.md`의 schema checklist를 따른다.
