# BriefTube

BriefTube는 YouTube 투자 콘텐츠를 수집하고, 자막을 기사 형태로 재구성해 로컬에서 확인하는 웹 앱 프로젝트입니다.

## 참고 문서

- [기획서](./01_기획서.md)
- [개발 스펙](./02_개발스펙.md)

## 예정 기술 스택

- Python 3.11+
- FastAPI + APScheduler
- SQLite (FTS5)
- Jinja2 + HTMX + Tailwind CSS

## 개발 범위 (요약)

- 채널 RSS 폴링으로 신규 영상 수집
- 자막 추출 및 키워드 필터링
- LLM 기반 기사 재구성
- SQLite 저장 및 검색
- 텔레그램 알림
