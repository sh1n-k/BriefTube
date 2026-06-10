# Audit Remediation Backlog

This document tracks the confirmed findings from the June 2026 read-only audit and
the remediation state for this workstream. Use `todo`, `in_progress`, `done`, or
`deferred` in the Status field.

## Contract

- Preserve existing behavior, public APIs, data formats, HTMX fragment contracts,
  remote-sync tombstone semantics, and test expectations.
- Do not overwrite user-owned local settings or generated data.
- Do not hard-delete remote-sync tombstones that must still be pushed.
- Do not log source text, raw model output, provider stdout/stderr bodies, secrets,
  or full Telegram response dictionaries.
- Keep changes minimal and update this backlog as each item is resolved or deferred.

## Confirmed Findings

### P1: Channel Metadata Retry Binding Error

Status: done

Evidence:
- `app/repositories/_channels_metadata.py` builds
  `metadata_fetch_status IN (?, ?)` but passes three status parameters.

Impact:
- Failed/rate-limited metadata retry enqueue can raise a SQLite binding error.

Done when:
- The placeholder count matches the parameter count.
- Deleted channels are not unintentionally requeued.
- A focused test covers failed/rate-limited metadata enqueue.

### P1: Channel Add/Bulk Add Drops Selected Category

Status: done

Evidence:
- Channel forms send `category_id`, but add and bulk-commit paths do not pass it to
  `channels_repo.add_channel`.
- `add_channel` defaults `category_id` to the default category when omitted.

Impact:
- Adding a channel from a category-filtered page stores it under the default
  category. Re-adding an existing channel can move it to the default category.

Done when:
- Single and bulk add preserve the selected category.
- Bulk resolve/commit preserves category context.
- Tests prove DB category assignment and refreshed fragments match the selected
  category.

### P1: Tombstoned Transcript/Article Rows Are Treated As Live

Status: done

Evidence:
- Video list `has_transcript`/`has_article`, video detail joins, manual transcript
  jobs, and manual article jobs check transcript/article existence without
  `deleted_at IS NULL` in several paths.

Impact:
- Deleted transcript/article tombstones can still appear in UI or cause manual jobs
  to skip/reuse stale data.

Done when:
- Live-data read paths ignore tombstoned transcript/article rows.
- Remote-sync push paths still keep tombstones available for synchronization.
- Tests cover list, detail, manual transcript, and manual article behavior.

### P1: LLM Failure Logging Can Expose Provider Output

Status: done

Evidence:
- Provider stdout/stderr are included in error messages and retryable failures are
  logged with tracebacks.

Impact:
- Source text, raw model output, prompt echoes, or provider output can leak into
  logs despite the logging policy.

Done when:
- Runtime issue and exception messages are sanitized.
- Raw stdout/stderr body logging is not used outside explicit capture paths.
- Sentinel-based tests prove logs do not contain provider output.

### P2: Channel Category Filter Is Lost After Mutations

Status: done

Evidence:
- Delete/reactivate routes and single-action HTMX URLs preserve status but not
  category context.

Impact:
- Category-filtered channel lists can be replaced by all-channel fragments after
  delete/reactivate actions.

Done when:
- Single and bulk delete/reactivate preserve `category_id`.
- `#channel-list-wrap` refresh URLs keep the selected category.
- Toast and `HX-Trigger` contracts remain unchanged.

### P2: Channel Import Refresh Misses Category UI Contracts

Status: done

Evidence:
- Import can create categories, but the success path refreshes only the channel
  list by status and does not refresh category sidebar or RSS preview.

Impact:
- Created categories, category counts, selected category state, and RSS preview can
  become stale until a full reload.

Done when:
- Import success refreshes the same relevant UI surfaces as channel add/bulk
  commit, or otherwise preserves category state and updates sidebar/preview.
- A view or E2E test covers category import UI refresh.

### P2: Channel JSON APIs Lack Robust Payload Validation

Status: done

Evidence:
- Several `/api/channels` endpoints call `await request.json()` and then assume the
  payload shape.

Impact:
- Malformed JSON or wrong top-level types can produce server errors instead of
  client-facing 400 responses.

Done when:
- Malformed JSON and wrong payload types return 400 with stable error details.
- Existing valid payload behavior is unchanged.

### P2: Deleted Videos Affect LLM Counts And Alerts

Status: done

Evidence:
- LLM pending/config/schema alert count queries do not filter `videos.deleted_at`.

Impact:
- Deleted `llm_pending`/`llm_failed` tombstones can keep settings counts or alerts
  alive.

Done when:
- LLM counts and alert decisions ignore tombstoned videos.
- Tests cover count and alert suppression for tombstoned videos.

### P2: Transcript Workers Do Not Share A Global External-I/O Gate

Status: done

Evidence:
- The regular transcript worker uses `state.transcript_worker_lock`, while manual
  transcript and manual article workers run their own request intervals/guard
  snapshots.

Impact:
- Concurrent transcript fetches can increase throttling risk and overwrite guard
  state updates.

Done when:
- All transcript-fetching worker paths coordinate through a shared gate or limiter.
- Tests prove concurrent workers do not call external transcript fetches at the
  same time and respect hard-throttle cooldown state.

### P2: Test Environments Do Not Fully Isolate Workers/Remote Sync

Status: done

Evidence:
- E2E uvicorn subprocess does not inherit `PYTEST_CURRENT_TEST`, while worker
  startup relies on that flag.
- The common TestClient fixture does not clear remote-sync environment variables.

Impact:
- E2E tests can be changed by background workers, and unit tests can attempt remote
  sync when shell/CI env variables are present.

Done when:
- E2E env disables all background workers unless explicitly enabled.
- Common tests isolate remote-sync env by default, while remote-sync tests opt in.
- Tests cover the isolation contract.

### P2: Legacy Video Rebuild Migration Drops Sync Metadata

Status: done

Evidence:
- The legacy videos table rebuild defines sync metadata columns but does not copy
  existing `updated_at`, `deleted_at`, `sync_dirty`, `sync_last_pushed_at`, or
  `origin_device_id` values.

Impact:
- Legacy DBs with both old pipeline columns and remote-sync tombstones can lose
  sync metadata during migration.

Done when:
- Rebuild migration preserves available sync metadata with safe fallbacks.
- A migration test covers legacy sync metadata preservation.

### P2: Remote-Sync Soft-Deleted Category Names Cannot Be Reused

Status: done

Evidence:
- `categories.name` is globally unique while remote-sync deletion leaves a
  tombstone row.

Impact:
- A user cannot recreate a deleted category with the same name when remote sync is
  active.

Done when:
- Active category names remain unique while tombstoned names can be reused.
- Migration/schema/test coverage preserve this contract.

## Performance And Operational Risks

### R1: Local Remote-Sync Dirty Scan Lacks Matching Indexes

Status: done

Evidence:
- Dirty scans use `sync_dirty = 1 ORDER BY updated_at LIMIT ?` across sync tables.
- Local schema lacks matching `(sync_dirty, updated_at)` indexes.

Done when:
- Narrow indexes support dirty scan query shapes without changing data contracts.
- Schema and migration helpers stay aligned.

### R2: Startup Remote Pull Ignores Batch Size

Status: done

Evidence:
- `RemoteSyncGateway.fetch_all(batch_size=...)` ignores the batch size and loads all
  rows ordered by `updated_at`.

Done when:
- Startup pull is bounded or intentionally documented/deferred with rationale.
- Remote-sync tests reflect the chosen contract.

### R3: Retention Queries Include Tombstoned Videos

Status: done

Evidence:
- Retention count/list queries filter by `upload_time` but not `deleted_at`.

Done when:
- Retention UI/count/list ignores tombstoned videos.
- Tests cover tombstoned expired videos.

### R4: Remote-Sync YAML Loading Contract Is Ambiguous

Status: done

Evidence:
- README describes YAML config precedence broadly, while remote-sync runtime fields
  are environment-only in the current loader.

Done when:
- Code and documentation agree.
- Tests cover the intended config source behavior.

### R5: LaunchAgent Dry-Run And XML Escaping Are Weakly Covered

Status: done

Evidence:
- LaunchAgent scripts parse YAML separately and generate plist XML directly.

Done when:
- Dry-run behavior is tested without destructive operations, or the remaining risk
  is documented as deferred with rationale.

## Cleanup Checklist

Status: done

- Remove only artifacts created by this workstream.
- Do not delete `data*.db`, `logs/`, `thumbnails*/`, downloads, local config, or
  user-owned generated output without explicit confirmation.
- Remove temporary test files, debug prints, unused helpers/imports, and stale
  intermediate artifacts before completion.
- Keep this backlog updated with final statuses.
