from __future__ import annotations

import asyncio
import os

from fastapi.testclient import TestClient

from app.database import open_database
from app.repositories import transcripts as repository
from tests.helpers.app_repo import call_repo


def test_transcript_guard_state_defaults(client: TestClient) -> None:
    guard = call_repo(client, repository.get_transcript_guard_state)
    assert guard["adaptive_factor"] == 1.0
    assert guard["breaker_state"] == "closed"
    assert guard["cooldown_until"] is None
    assert guard["half_open_probe_remaining"] == 1


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

    after_unconfirmed = call_repo(client, repository.get_transcript_guard_state)
    assert after_unconfirmed["adaptive_factor"] == 2.0

    confirmed = client.post(
        "/settings/transcript-guard/reset",
        data={"confirm_guard_reset": "on"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert confirmed.headers["location"] == "/settings?guard_reset=1"

    after_confirmed = call_repo(client, repository.get_transcript_guard_state)
    assert after_confirmed["adaptive_factor"] == 1.0
    assert after_confirmed["breaker_state"] == "closed"
    assert after_confirmed["cooldown_until"] is None
