from __future__ import annotations

from app.services.remote_sync import run_push_loop
from app.state import AppState


async def run_remote_sync_worker(state: AppState) -> None:
    await run_push_loop(state.config, state.db)
