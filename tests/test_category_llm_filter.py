from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.database import init_database, open_database
from app import repository


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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


def _add_video_with_transcript(db, video_id: str, channel_id: str, status: str = "llm_pending") -> None:
    db.run(db.conn.execute(
        "INSERT INTO videos (video_id, channel_id, title, upload_time, pipeline_status) VALUES (?, ?, ?, datetime('now'), ?)",
        (video_id, channel_id, f"Test {video_id}", status),
    ))
    db.run(db.conn.execute(
        "INSERT INTO transcripts (video_id, raw_text, source_type) VALUES (?, ?, ?)",
        (video_id, f"Transcript for {video_id}", "auto"),
    ))
    db.run(db.conn.commit())


def test_llm_disabled_category_excluded_from_pop(db) -> None:
    cat = db.run(repository.create_category(db.conn, "LLM비활성"))
    db.run(repository.update_category_llm_enabled(db.conn, cat["id"], False))
    _add_channel(db, "UC_llm_off", "LLM Off Channel")
    db.run(repository.move_channels_to_category(db.conn, ["UC_llm_off"], cat["id"]))
    _add_video_with_transcript(db, "vid_llm_off", "UC_llm_off")

    candidate = db.run(repository.pop_llm_candidate(db.conn, max_retry_count=3))
    assert candidate is None


def test_llm_enabled_category_included_in_pop(db) -> None:
    cat = db.run(repository.create_category(db.conn, "LLM활성"))
    db.run(repository.update_category_llm_enabled(db.conn, cat["id"], True))
    _add_channel(db, "UC_llm_on", "LLM On Channel")
    db.run(repository.move_channels_to_category(db.conn, ["UC_llm_on"], cat["id"]))
    _add_video_with_transcript(db, "vid_llm_on", "UC_llm_on")

    candidate = db.run(repository.pop_llm_candidate(db.conn, max_retry_count=3))
    assert candidate is not None
    assert candidate["video_id"] == "vid_llm_on"


def test_null_category_treated_as_enabled(db) -> None:
    db.run(db.conn.execute(
        "INSERT INTO channels (channel_id, channel_name, rss_url) VALUES (?, ?, ?)",
        ("UC_null_cat", "Null Cat Channel", "https://example.com/rss"),
    ))
    db.run(db.conn.execute(
        "UPDATE channels SET category_id = NULL WHERE channel_id = 'UC_null_cat'",
    ))
    db.run(db.conn.commit())
    _add_video_with_transcript(db, "vid_null_cat", "UC_null_cat")

    candidate = db.run(repository.pop_llm_candidate(db.conn, max_retry_count=3))
    assert candidate is not None
    assert candidate["video_id"] == "vid_null_cat"


def test_count_llm_pending_respects_category(db) -> None:
    cat_off = db.run(repository.create_category(db.conn, "카운트비활성"))
    db.run(repository.update_category_llm_enabled(db.conn, cat_off["id"], False))
    _add_channel(db, "UC_cnt_off", "Count Off")
    db.run(repository.move_channels_to_category(db.conn, ["UC_cnt_off"], cat_off["id"]))
    _add_video_with_transcript(db, "vid_cnt_off", "UC_cnt_off")

    cat_on = db.run(repository.create_category(db.conn, "카운트활성"))
    _add_channel(db, "UC_cnt_on", "Count On")
    db.run(repository.move_channels_to_category(db.conn, ["UC_cnt_on"], cat_on["id"]))
    _add_video_with_transcript(db, "vid_cnt_on", "UC_cnt_on")

    count = db.run(repository.count_llm_pending_videos(db.conn))
    assert count == 1
