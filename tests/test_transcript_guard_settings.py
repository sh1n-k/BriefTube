from __future__ import annotations

import asyncio
import os

from fastapi.testclient import TestClient

from app import repository
from app.database import open_database


def test_get_settings_includes_transcript_guard_defaults(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()

    assert "transcript_guard" in payload
    assert payload["transcript_guard"] == {
        "adaptive_factor": 1.0,
        "cooldown_until": None,
        "consecutive_hard_errors": 0,
        "consecutive_successes": 0,
    }


def test_reset_transcript_guard_api_resets_persisted_state(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]

    async def _seed() -> None:
        db = await open_database(db_path)
        try:
            await repository.save_transcript_guard_state(
                db,
                adaptive_factor=4.0,
                cooldown_until="2099-01-01T00:00:00+00:00",
                consecutive_hard_errors=3,
                consecutive_successes=1,
            )
        finally:
            await db.close()

    asyncio.run(_seed())

    before = client.get("/api/settings")
    assert before.status_code == 200
    assert before.json()["transcript_guard"]["adaptive_factor"] == 4.0

    reset = client.post("/api/settings/transcript-guard/reset")
    assert reset.status_code == 200
    assert reset.json()["ok"] is True
    assert reset.json()["transcript_guard"] == {
        "adaptive_factor": 1.0,
        "cooldown_until": None,
        "consecutive_hard_errors": 0,
        "consecutive_successes": 0,
    }


def test_settings_page_reset_requires_confirmation(client: TestClient) -> None:
    db_path = os.environ["DB_PATH"]

    async def _seed() -> None:
        db = await open_database(db_path)
        try:
            await repository.save_transcript_guard_state(
                db,
                adaptive_factor=2.0,
                cooldown_until="2099-01-01T00:00:00+00:00",
                consecutive_hard_errors=2,
                consecutive_successes=0,
            )
        finally:
            await db.close()

    asyncio.run(_seed())

    response = client.post("/settings/transcript-guard/reset", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?guard_reset=0"

    after_unconfirmed = client.get("/api/settings").json()["transcript_guard"]
    assert after_unconfirmed["adaptive_factor"] == 2.0

    confirmed = client.post(
        "/settings/transcript-guard/reset",
        data={"confirm_guard_reset": "on"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert confirmed.headers["location"] == "/settings?guard_reset=1"

    after_confirmed = client.get("/api/settings").json()["transcript_guard"]
    assert after_confirmed["adaptive_factor"] == 1.0
    assert after_confirmed["cooldown_until"] is None
