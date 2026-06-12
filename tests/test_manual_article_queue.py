from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.database import init_database, open_database
from app.repositories import manual_articles as manual_articles_repo
from app.repositories import videos as videos_repo
from tests.helpers.db_seed import seed_channel, seed_video

CHANNEL_ID = "UCmanual001"
repository = SimpleNamespace(
    enqueue_manual_article_jobs=manual_articles_repo.enqueue_manual_article_jobs,
    claim_next_manual_article_job=manual_articles_repo.claim_next_manual_article_job,
    mark_manual_article_job_succeeded=manual_articles_repo.mark_manual_article_job_succeeded,
    recover_stuck_manual_article_jobs=manual_articles_repo.recover_stuck_manual_article_jobs,
    get_manual_article_job=manual_articles_repo.get_manual_article_job,
    get_video=videos_repo.get_video,
)


async def _seed_channel(db) -> None:
    await seed_channel(db, channel_id=CHANNEL_ID, channel_name="Manual Channel")


async def _insert_video(db, *, video_id: str, pipeline_status: str) -> None:
    await seed_video(db, video_id=video_id, channel_id=CHANNEL_ID, pipeline_status=pipeline_status)


def test_enqueue_manual_article_jobs_summary(tmp_path) -> None:
    db_path = tmp_path / "manual-enqueue.db"

    async def _run() -> tuple[dict[str, object], list[tuple[str, str]]]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-retry-001", pipeline_status="transcript_failed")
        await _insert_video(db, video_id="vid-skip-001", pipeline_status="llm_pending")
        await _insert_video(db, video_id="vid-new-001", pipeline_status="archived")
        await _insert_video(db, video_id="vid-dup-001", pipeline_status="llm_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-dup-001",),
        )
        await db.commit()

        summary = await repository.enqueue_manual_article_jobs(
            db,
            [
                "vid-retry-001",
                "vid-skip-001",
                "vid-new-001",
                "vid-dup-001",
                "",
                "vid-missing-001",
            ],
        )
        cursor = await db.execute(
            """
            SELECT video_id, status
            FROM manual_article_jobs
            ORDER BY id ASC
            """
        )
        rows = [(str(row["video_id"]), str(row["status"])) for row in await cursor.fetchall()]
        await db.close()
        return summary, rows

    summary, rows = asyncio.run(_run())
    assert summary["new"] == ["vid-new-001"]
    assert summary["retry"] == ["vid-retry-001"]
    assert set(summary["skip"]) == {"vid-skip-001", "vid-dup-001"}
    assert set(summary["failed"]) == {"", "vid-missing-001"}
    assert summary["new_count"] == 1
    assert summary["retry_count"] == 1
    assert summary["skip_count"] == 2
    assert summary["failed_count"] == 2
    assert rows == [
        ("vid-dup-001", "pending"),
        ("vid-retry-001", "pending"),
        ("vid-new-001", "pending"),
    ]


def test_manual_article_claim_mark_and_recover(tmp_path) -> None:
    db_path = tmp_path / "manual-claim.db"

    async def _run() -> tuple[dict[str, object] | None, str, int]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-claim-001", pipeline_status="transcript_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-claim-001",),
        )
        await db.commit()

        claimed = await repository.claim_next_manual_article_job(db)
        assert claimed is not None
        assert claimed["status"] == "running"
        updated = await repository.mark_manual_article_job_succeeded(db, job_id=int(claimed["id"]))
        assert updated == 1

        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, updated_at)
            VALUES (?, 'pending', datetime('now'), datetime('now'))
            """,
            ("vid-claim-001",),
        )
        await db.commit()
        claimed_again = await repository.claim_next_manual_article_job(db)
        assert claimed_again is not None
        await db.execute(
            """
            UPDATE manual_article_jobs
            SET
                started_at = datetime('now', '-2 hours'),
                updated_at = datetime('now', '-2 hours')
            WHERE id = ?
            """,
            (int(claimed_again["id"]),),
        )
        await db.commit()
        recovered = await repository.recover_stuck_manual_article_jobs(
            db,
            stale_after_seconds=3600,
        )
        row = await repository.get_manual_article_job(db, int(claimed_again["id"]))
        assert row is not None
        status = str(row["status"])
        await db.close()
        return claimed, status, recovered

    claimed, status, recovered = asyncio.run(_run())
    assert claimed is not None
    assert status == "failed"
    assert recovered == 1


def test_recover_stuck_manual_article_jobs_recovers_only_stale_running(tmp_path) -> None:
    db_path = tmp_path / "manual-recover-threshold.db"

    async def _run() -> tuple[int, dict[str, tuple[str, str]]]:
        db = await open_database(str(db_path))
        await init_database(db)
        await _seed_channel(db)
        await _insert_video(db, video_id="vid-stale-001", pipeline_status="transcript_failed")
        await _insert_video(db, video_id="vid-fresh-001", pipeline_status="transcript_failed")
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, started_at, updated_at)
            VALUES (
                ?,
                'running',
                datetime('now', '-4 hours'),
                datetime('now', '-4 hours'),
                datetime('now', '-4 hours')
            )
            """,
            ("vid-stale-001",),
        )
        await db.execute(
            """
            INSERT INTO manual_article_jobs(video_id, status, requested_at, started_at, updated_at)
            VALUES (?, 'running', datetime('now'), datetime('now'), datetime('now'))
            """,
            ("vid-fresh-001",),
        )
        await db.commit()

        recovered = await repository.recover_stuck_manual_article_jobs(
            db,
            stale_after_seconds=3600,
        )
        cursor = await db.execute(
            """
            SELECT video_id, status, error_message
            FROM manual_article_jobs
            WHERE video_id IN (?, ?)
            ORDER BY id ASC
            """,
            ("vid-stale-001", "vid-fresh-001"),
        )
        rows = await cursor.fetchall()
        await db.close()
        return recovered, {
            str(row["video_id"]): (str(row["status"]), str(row["error_message"] or ""))
            for row in rows
        }

    recovered, statuses = asyncio.run(_run())
    assert recovered == 1
    assert statuses["vid-stale-001"][0] == "failed"
    assert "stale timeout exceeded" in statuses["vid-stale-001"][1]
    assert statuses["vid-fresh-001"][0] == "running"
    assert statuses["vid-fresh-001"][1] == ""
