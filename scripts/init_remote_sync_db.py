from __future__ import annotations

import argparse
import asyncio

from app.config import load_config
from app.services.remote_sync import RemoteSyncGateway


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the BriefTube remote sync schema.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate config without touching remote DB."
    )
    args = parser.parse_args()
    config = load_config()
    if not config.remote_sync_dsn:
        raise SystemExit("BRIEFTUBE_REMOTE_SYNC_DSN is not set")
    if args.dry_run:
        print("remote sync DSN is configured; dry-run did not connect")
        return
    gateway = RemoteSyncGateway(
        dsn=config.remote_sync_dsn,
        connect_timeout_seconds=config.remote_sync_connect_timeout_seconds,
    )
    await gateway.ensure_schema()
    print("remote sync schema is ready")


if __name__ == "__main__":
    asyncio.run(_main())
