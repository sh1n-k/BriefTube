# BriefTube 코드 리뷰 / 보안 감사 결과 (2026-02-25)

## 0) 감사 범위와 기준
- 범위: `app/*`, `scripts/*`, `sql/schema.sql`, `tests/*`
- 기준:
  - 기본 운영 가정: 로컬 단일 사용자
  - 확장 가정: 운영 스크립트 사용 시 네트워크 노출 가능성
  - 핵심 목표: "크래시 없이 계속 동작" + 로컬/노출 환경 모두에서의 안전성
- 확인 명령:
  - `.venv/bin/python -m pytest -q`
  - 추가 재현: malformed search query 호출(아래 F-04)

---

## 1) 핵심 결론
- 로컬 단일 사용자만 가정하면 즉시 치명적 취약점은 낮지만, **현재 상태를 그대로 노출 운영하면 고위험**이다.
- 무중단 관점에서 가장 큰 리스크는:
  1. 인증/접근제어 부재
  2. 업로드/요청 제한 부재에 따른 자원 고갈 가능성
  3. 일부 예외의 미처리(요청 500) 및 워커 단위 정체 가능성

---

## 2) 발견 사항 (심각도 순)

### F-01. 인증/인가 부재로 변경성 API가 무방비
- 심각도: **높음 (로컬), 치명적 (노출 시)**
- 근거:
  - 라우터 등록만 있고 인증 계층 없음: `app/main.py:91-94`
  - 변경성 엔드포인트 예:
    - 채널 추가/삭제: `app/routers/api.py:38-60`, `158-172`
    - 워커/정책/설정 변경: `app/routers/api.py:273-399`
    - 보관 삭제/채널 삭제 등 뷰 POST: `app/routers/views.py:46-89`, `165-242`
    - 보관 삭제 페이지 POST: `app/routers/pages.py:142-178`
- 영향:
  - 접근 가능한 환경에서는 임의 사용자가 데이터 삭제/설정 변경/폴링 트리거 가능
- 권고:
  - 최소 단일 토큰(Bearer) 인증 도입
  - 변경성 엔드포인트는 인증 + 감사 로그 필수

### F-02. 운영 스크립트 기본 바인딩이 `0.0.0.0`
- 심각도: **높음**
- 근거:
  - `scripts/run-prod.sh:8` (`HOST="${HOST:-0.0.0.0}"`)
- 영향:
  - 인증 없는 상태에서 외부 접근 가능 범위가 크게 확대
- 권고:
  - 기본값 `127.0.0.1`로 변경하거나, 실행 전 명시적 확인 단계 추가
  - 노출 운영 시 역프록시 + 인증 + TLS 조합 필수

### F-03. 업로드 파일 크기 제한 부재(메모리 고갈 가능)
- 심각도: **중간**
- 근거:
  - API 경로 업로드 전체 메모리 read: `app/routers/api.py:78-83`
  - Views 경로 업로드 전체 메모리 read: `app/routers/views.py:171-174`
  - 파서도 전체 decode 처리: `app/services/takeout_parser.py:83-90`, `136-162`
- 영향:
  - 큰 파일 반복 업로드 시 메모리 압박/응답 지연/프로세스 불안정
- 권고:
  - `Content-Length` + 실제 바이트 크기 상한 도입
  - 업로드 크기 초과 시 즉시 413 처리

### F-04. 검색 쿼리 malformed 입력 시 500 (예외 미처리)
- 심각도: **중간**
- 근거:
  - 검색 API: `app/routers/api.py:221-223`
  - FTS 질의 실행: `app/repository.py:341-370`
  - 재현 결과: `q="` 입력 시 `sqlite3.OperationalError: unterminated string`
- 영향:
  - 요청 단위 500, 로그 노이즈 증가, 반복 호출 시 운영 가시성 저하
- 권고:
  - `OperationalError`를 400으로 변환
  - FTS 특수문자 전처리/이스케이프 정책 도입

### F-05. 비용 큰 엔드포인트에 레이트리밋 부재
- 심각도: **중간**
- 근거:
  - bulk resolve/commit: `app/routers/api.py:63-102`, `120-155`
  - 수동 폴링 트리거: `app/routers/api.py:226-231`
  - 뷰 bulk resolve/commit: `app/routers/views.py:165-242`
- 영향:
  - 과도 호출 시 외부 요청 폭주/DB 부하/서비스 체감 저하
- 권고:
  - IP/토큰 기준 버킷형 레이트리밋(특히 bulk/trigger 경로)

### F-06. Transcript fetch 무제한 대기 가능성(워커 정체)
- 심각도: **중간 (가용성)**
- 근거:
  - `YouTubeTranscriptApi.fetch`를 `to_thread`로 호출하지만 timeout 제어 없음: `app/services/transcript.py:16`
  - 워커가 해당 호출 완료를 기다림: `app/workers/transcript_worker.py:210-213`
- 영향:
  - 특정 요청이 장시간 멈추면 transcript 워커 진행이 장시간 정체 가능
- 권고:
  - fetch 단계 타임아웃/취소 전략(작업별 최대 시간) 도입
  - 정체 감지 로그(경과 시간 기반) 추가

### F-07. 광범위 `except Exception` 사용으로 실패 원인 가시성 저하
- 심각도: **낮음~중간 (운영성)**
- 근거:
  - 워커 루프 전반: `app/workers/poller.py:40,58,107`, `app/workers/transcript_worker.py:246,314`, `app/workers/llm_worker.py:48,60`, `app/workers/notifier_worker.py:54`
  - 파서 래퍼에서 예외 삼킴: `app/services/bulk_channels.py:115`
- 영향:
  - 크래시는 막지만, 결함 패턴이 누적되어도 원인 분석이 늦어질 수 있음
- 권고:
  - 예외 유형별 분기 + 메트릭/카운터 로그 추가
  - "복구 가능/불가" 분리 처리

### F-08. 테스트 기준선 불안정(현재 1건 실패)
- 심각도: **중간 (품질 게이트)**
- 근거:
  - 실패 테스트: `tests/test_settings_views.py:28-29` 기대 문구 불일치
  - 최근 실행 결과: `1 failed, 53 passed`
- 영향:
  - 회귀 탐지 신뢰도 저하, 배포/릴리스 판단 지연
- 권고:
  - 현재 UI 문구와 테스트 기대치를 동기화해 기준선 복구

---

## 3) 긍정적 확인 사항 (잘된 점)
- 썸네일 경로 검증으로 경로 조작 방어:
  - `app/main.py:102-115`, `app/routers/views.py:19-32`, `app/routers/pages.py:126-139`
- SQL 파라미터 바인딩이 기본이며 정렬 컬럼 whitelist 처리:
  - `app/repository.py:186-188`, `190-232`
- DB 무결성/운영성 설정:
  - Foreign key + WAL + synchronous NORMAL: `app/database.py:13-15`
- Transcript 요청 과속 방지 로직(백오프/쿨다운) 존재:
  - `app/workers/transcript_worker.py:55-73`, `271-313`

---

## 4) 우선순위 권고 (무중단 중심)
- P0 (즉시): F-01, F-02
- P1 (단기): F-03, F-04, F-05
- P2 (단기~중기): F-06, F-07, F-08

---

## 5) 감사 메모
- 본 감사는 **코드 수정 없이** 정적 검토 + 테스트 실행 기반으로 작성함.
- 로컬 전용 운용이라도, 운영 스크립트 기본값/네트워크 환경 변화로 노출될 수 있으므로 P0 항목은 선제 대응 권장.
