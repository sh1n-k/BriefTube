# LLM 재구조화 프롬프트 버전

Settings DB(`app_settings.llm_prompt_template`)에 들어가는 재구조화 프롬프트의
버전 스냅샷과 적용/롤백 방법입니다. 런타임 기본값은 DB이며, 이 디렉터리는
감사·롤백용 원본입니다.

## 버전

| 파일 | 용도 |
|---|---|
| `versions/2026-08-07-prod-baseline.txt` | 적용 직전 prod 스냅샷 (롤백 기준) |
| `versions/2026-08-07-dev-baseline.txt` | 적용 직전 dev 스냅샷 |
| `versions/2026-08-07-reader-clarity-v1.txt` | 독자 이해 보강 규칙 추가본 |

## 적용 / 롤백

적용 전 현재 DB 값이 `backups/`에 자동 저장됩니다. `backups/`는 gitignore 대상입니다.

```bash
# 목록
uv run python scripts/apply_llm_prompt.py list

# dry-run (기본)
uv run python scripts/apply_llm_prompt.py apply \
  --db data.prod.db \
  --version 2026-08-07-reader-clarity-v1

# 실제 적용
uv run python scripts/apply_llm_prompt.py apply \
  --db data.prod.db \
  --version 2026-08-07-reader-clarity-v1 \
  --write

# 롤백 (prod baseline)
uv run python scripts/apply_llm_prompt.py apply \
  --db data.prod.db \
  --version 2026-08-07-prod-baseline \
  --write

# 현재 DB 값만 백업
uv run python scripts/apply_llm_prompt.py backup --db data.prod.db
```

운영/공용 DB 쓰기는 명시 승인 후에만 `--write`로 실행합니다.
