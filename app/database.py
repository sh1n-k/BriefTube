from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.database_migrations import run_database_migrations

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


async def open_database(db_path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path, timeout=5.0)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA synchronous = NORMAL;")
    await db.execute("PRAGMA busy_timeout = 5000;")
    return db


@asynccontextmanager
async def database_transaction(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Own an isolated SQLite write transaction on a dedicated connection.

    Multi-statement use cases use this boundary instead of the process-wide
    connection so another coroutine cannot accidentally commit their work.
    """
    db = await open_database(db_path)
    try:
        await db.execute("BEGIN IMMEDIATE")
        yield db
    except BaseException:
        await db.rollback()
        raise
    else:
        await db.commit()
    finally:
        await db.close()


async def init_database(db: aiosqlite.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    await db.executescript(schema_sql)
    await run_database_migrations(db)


async def recover_stuck_jobs(db: aiosqlite.Connection) -> int:
    llm_cursor = await db.execute(
        """
        UPDATE videos
        SET pipeline_status = 'llm_pending'
        WHERE pipeline_status = 'llm_processing'
        """
    )
    await db.commit()
    return int(llm_cursor.rowcount or 0)
