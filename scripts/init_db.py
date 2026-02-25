from __future__ import annotations

import asyncio

from app.config import load_config
from app.database import init_database, open_database


async def main() -> None:
    cfg = load_config()
    db = await open_database(cfg.db_path)
    try:
        await init_database(db)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
