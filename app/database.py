from __future__ import annotations

from pathlib import Path
import aiosqlite


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


async def open_database(db_path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA synchronous = NORMAL;")
    return db


async def init_database(db: aiosqlite.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    await db.executescript(schema_sql)
    await db.commit()


async def recover_stuck_jobs(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE videos
        SET restructure_status = 'pending'
        WHERE restructure_status = 'processing'
        """
    )
    await db.commit()
    return cursor.rowcount
