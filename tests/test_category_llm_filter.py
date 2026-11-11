from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import init_database, open_database
from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from app.repositories import pipeline as pipeline_repo
from app.repositories import videos as videos_repo

repository = SimpleNamespace(
    add_channel=channels_repo.add_channel,
    insert_video_if_absent=videos_repo.insert_video_if_absent,
    complete_transcript_job=pipeline_repo.complete_transcript_job,
    create_category=categories_repo.create_category,
    move_channels_to_category=categories_repo.move_channels_to_category,
    update_category_processing_stage=categories_repo.update_category_processing_stage,
    claim_next_article_job=pipeline_repo.claim_next_article_job,
    count_queued_article_jobs=pipeline_repo.count_queued_article_jobs,
)


@pytest.fixture()
def db(tmp_path: Path):
    loop = asyncio.new_event_loop()
    conn = loop.run_until_complete(open_database(str(tmp_path / "test_llm.db")))
    loop.run_until_complete(init_database(conn))

    class _DB:
        def __init__(self, conn, loop):
            self._conn = conn
            self._loop = loop

        def run(self, coro):
            return self._loop.run_until_complete(coro)

        @property
        def conn(self):
            return self._conn

    wrapper = _DB(conn, loop)
    yield wrapper
    loop.run_until_complete(conn.close())
    loop.close()


def _add_channel(db, channel_id: str, name: str) -> dict:
    return db.run(repository.add_channel(db.conn, channel_id=channel_id, channel_name=name))


def _insert_video(db, *, video_id: str, channel_id: str) -> None:
    inserted = db.run(
        repository.insert_video_if_absent(
            db.conn,
            video_id=video_id,
            channel_id=channel_id,
            title=f"Test {video_id}",
            upload_time="2026-02-20T00:00:00+00:00",
        )
    )
    assert inserted is True


def _transcript_job_ids(db, video_id: str) -> list[int]:
    cursor = db.run(
        db.conn.execute(
            "SELECT id FROM video_jobs WHERE video_id = ? AND kind = 'transcript' ORDER BY id",
            (video_id,),
        )
    )
    return [int(row["id"]) for row in db.run(cursor.fetchall())]


def _save_transcript(db, *, video_id: str) -> None:
    job_ids = _transcript_job_ids(db, video_id)
    assert len(job_ids) == 1
    db.run(
        repository.complete_transcript_job(
            db.conn,
            job_id=job_ids[0],
            video_id=video_id,
            raw_text=f"Transcript for {video_id}",
            language="ko",
            source_type="auto",
            thumbnail_path=None,
        )
    )


def _video_state(db, video_id: str) -> tuple[str, str]:
    cursor = db.run(
        db.conn.execute(
            """
            SELECT p.pipeline_status, v.target_stage
            FROM videos v
            JOIN video_pipeline_v p ON p.video_id = v.video_id
            WHERE v.video_id = ?
            """,
            (video_id,),
        )
    )
    row = db.run(cursor.fetchone())
    assert row is not None
    return str(row["pipeline_status"]), str(row["target_stage"])


def test_processing_stage_off_inserts_paused(db) -> None:
    cat = db.run(repository.create_category(db.conn, "중지"))
    _add_channel(db, "UC_stage_off", "Stage Off")
    db.run(repository.move_channels_to_category(db.conn, ["UC_stage_off"], cat["id"]))
    _insert_video(db, video_id="vid_stage_off", channel_id="UC_stage_off")

    status, target_stage = _video_state(db, "vid_stage_off")
    assert status == "paused"
    assert target_stage == "none"
    assert _transcript_job_ids(db, "vid_stage_off") == []


def test_processing_stage_transcript_only_stops_at_transcript_ready(db) -> None:
    cat = db.run(repository.create_category(db.conn, "자막전용"))
    db.run(repository.update_category_processing_stage(db.conn, cat["id"], "transcript_only"))
    _add_channel(db, "UC_stage_tx", "Stage Transcript Only")
    db.run(repository.move_channels_to_category(db.conn, ["UC_stage_tx"], cat["id"]))
    _insert_video(db, video_id="vid_stage_tx", channel_id="UC_stage_tx")
    _save_transcript(db, video_id="vid_stage_tx")

    status, target_stage = _video_state(db, "vid_stage_tx")
    assert status == "transcript_ready"
    assert target_stage == "transcript"
    candidate = db.run(repository.claim_next_article_job(db.conn, max_attempts=3))
    assert candidate is None


def test_processing_stage_full_queues_llm_candidate(db) -> None:
    cat = db.run(repository.create_category(db.conn, "전체처리"))
    db.run(repository.update_category_processing_stage(db.conn, cat["id"], "full"))
    _add_channel(db, "UC_stage_full", "Stage Full")
    db.run(repository.move_channels_to_category(db.conn, ["UC_stage_full"], cat["id"]))
    _insert_video(db, video_id="vid_stage_full", channel_id="UC_stage_full")
    _save_transcript(db, video_id="vid_stage_full")

    status, target_stage = _video_state(db, "vid_stage_full")
    assert status == "article_pending"
    assert target_stage == "article"
    candidate = db.run(repository.claim_next_article_job(db.conn, max_attempts=3))
    assert candidate is not None
    assert candidate["video_id"] == "vid_stage_full"


def test_count_llm_pending_counts_only_full_stage(db) -> None:
    cat_full = db.run(repository.create_category(db.conn, "카운트전체"))
    db.run(repository.update_category_processing_stage(db.conn, cat_full["id"], "full"))
    _add_channel(db, "UC_cnt_full", "Count Full")
    db.run(repository.move_channels_to_category(db.conn, ["UC_cnt_full"], cat_full["id"]))
    _insert_video(db, video_id="vid_cnt_full", channel_id="UC_cnt_full")
    _save_transcript(db, video_id="vid_cnt_full")

    cat_tx = db.run(repository.create_category(db.conn, "카운트자막"))
    db.run(repository.update_category_processing_stage(db.conn, cat_tx["id"], "transcript_only"))
    _add_channel(db, "UC_cnt_tx", "Count Transcript")
    db.run(repository.move_channels_to_category(db.conn, ["UC_cnt_tx"], cat_tx["id"]))
    _insert_video(db, video_id="vid_cnt_tx", channel_id="UC_cnt_tx")
    _save_transcript(db, video_id="vid_cnt_tx")

    # A legacy article job without a transcript is settled at claim time and must not
    # count as waiting work (it would raise the LLM-blocked alert/toast for nothing).
    _insert_video(db, video_id="vid_cnt_orphan", channel_id="UC_cnt_full")
    db.run(
        db.conn.execute(
            "UPDATE video_jobs SET status = 'cancelled' WHERE video_id = 'vid_cnt_orphan'"
        )
    )
    db.run(
        db.conn.execute(
            "INSERT INTO video_jobs(video_id, kind, origin, status) "
            "VALUES ('vid_cnt_orphan', 'article', 'auto', 'queued')"
        )
    )
    db.run(db.conn.commit())

    count = db.run(repository.count_queued_article_jobs(db.conn))
    assert count == 1
