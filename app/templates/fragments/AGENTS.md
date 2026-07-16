# HTMX fragment rules

- fragment root id/data attribute, route의 template 이름, `hx-target`/`hx-swap`과 JS selector를 하나의 계약으로 취급한다.
- OOB fragment는 기존 target id와 swap 범위를 유지하고 관련 unit contract와 E2E를 함께 갱신한다.
- DOM `scrollTop`은 숨기기 전에 저장하고 표시 후 복원한다.
- HTMX swap 뒤 필요한 동작은 전역 load order에 기대지 말고 기존 hydrate/bind 진입점에서 재적용한다.
