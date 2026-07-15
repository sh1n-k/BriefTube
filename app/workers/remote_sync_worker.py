from __future__ import annotations

from app.database import open_database
from app.services.remote_sync import run_push_loop
from app.state import AppState


async def run_remote_sync_worker(state: AppState) -> None:
    db = await open_database(state.config.db_path)
    try:
        await run_push_loop(state.config, db)
    finally:
        await db.close()
